"""V3 training: teach the ordering of legal moves, score it on the harmful tail.

This extends `nnue_v2_train` rather than replacing it. The feature extractor, the
sibling-group encoding, the int8 quantizer and the reference integer inference are
imported unchanged, because all four were proved correct in V1/V2 and re-deriving them
here would only create a second thing that can drift. What is new is the loss and the
selection metric.

V2's loss ranked sibling *pairs*. That is a local signal: it says nothing about the one
decision the engine actually makes, which is the argmin over the whole group. V3 adds
two objectives that act on the group as a unit:

- **listwise** -- match the teacher's whole score distribution over the legal moves;
- **regret-weighted top move** -- get the best move right, weighted by how much the
  control currently loses by getting it wrong, so the gradient goes where the damage is.

And V3 scores checkpoints on the tail of the regret they *add*, which a mean cannot see.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
from typing import Any

import numpy as np

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

import nnue_v2_model as nn_features  # noqa: E402
import nnue_v2_train as v2  # noqa: E402
import nnue_v3_metrics as v3m  # noqa: E402
import nnue_v3_relative as v3r  # noqa: E402

OUTROOT = REPO / "tests" / "results" / "nnue" / "v3"
OUTPUT_SCALE = v2.OUTPUT_SCALE
CORRECTION_CLAMP = v2.CORRECTION_CLAMP
PRESERVE_FLOOR = 0.97

# Softmax temperatures, in centipawns. The teacher distribution is sharper than the
# student's so the loss keeps a usable gradient on near-ties instead of saturating.
TEACHER_TEMP_CP = 60.0
STUDENT_TEMP_CP = 90.0

VARIANTS: dict[str, dict[str, Any]] = {
    # Every variant carries V2's anchor, draw and magnitude terms, because V2 measured
    # that those -- not the confidence gate -- are what buys preservation. The V3 axis
    # is which move-selection objective sits on top.
    "P": {"w_value": 0.3, "w_rank": 1.0, "w_list": 0.0, "w_top": 0.0,
          "w_anchor": 10.0, "w_draw": 4.0, "w_conf": 1.0, "w_quiet": 0.4,
          "w_zero": 0.0, "gate": True, "phase_gate": False},
    "L": {"w_value": 0.3, "w_rank": 0.0, "w_list": 1.0, "w_top": 0.0,
          "w_anchor": 10.0, "w_draw": 4.0, "w_conf": 1.0, "w_quiet": 0.4,
          "w_zero": 0.0, "gate": True, "phase_gate": False},
    "R": {"w_value": 0.0, "w_rank": 0.0, "w_list": 0.0, "w_top": 1.0,
          "w_anchor": 10.0, "w_draw": 4.0, "w_conf": 1.0, "w_quiet": 0.4,
          "w_zero": 0.0, "gate": True, "phase_gate": False},
    "RA": {"w_value": 0.3, "w_rank": 0.0, "w_list": 0.5, "w_top": 1.0,
           "w_anchor": 10.0, "w_draw": 4.0, "w_conf": 1.0, "w_quiet": 0.4,
           "w_zero": 0.0, "gate": True, "phase_gate": False},
    # RAP is RA plus the near-zero sign penalty. V2 traced the first fixture break to a
    # repetition defence, so this term exists to defend exactly that neighbourhood.
    "RAP": {"w_value": 0.3, "w_rank": 0.5, "w_list": 0.5, "w_top": 1.0,
            "w_anchor": 12.0, "w_draw": 6.0, "w_conf": 1.0, "w_quiet": 0.5,
            "w_zero": 4.0, "gate": True, "phase_gate": False},
}


def padded_groups(enc: dict[str, Any]) -> dict[str, np.ndarray]:
    """Dense (n_groups, K) row indices and a mask, so a group is one tensor slice.

    Groups hold at most MultiPV plus the control's own move, so K is small and the
    padded form costs almost nothing. It is what makes a listwise softmax expressible
    without a scatter-softmax dependency the platform does not ship.
    """
    offsets = enc["offsets"]
    n_groups = len(offsets) - 1
    sizes = np.diff(offsets)
    k = int(sizes.max())
    rows = np.zeros((n_groups, k), dtype=np.int64)
    mask = np.zeros((n_groups, k), dtype=np.float32)
    for gi in range(n_groups):
        lo, hi = int(offsets[gi]), int(offsets[gi + 1])
        span = hi - lo
        rows[gi, :span] = np.arange(lo, hi)
        # Padding points at the group's own first row, so a masked-out slot never reads
        # another group's data even though its contribution is multiplied away.
        rows[gi, span:] = lo
        mask[gi, :span] = 1.0
    return {"rows": rows, "mask": mask, "k": np.array(k)}


def train_variant(
    variant: str,
    hidden: int,
    seed: int,
    enc: dict[str, Any],
    pad: dict[str, np.ndarray],
    split_groups: dict[str, np.ndarray],
    epochs: int,
    batch_groups: int,
    lr: float,
    outdir: pathlib.Path,
    overrides: dict[str, float],
    relative: bool,
) -> dict[str, Any]:
    import torch
    from torch import nn

    cfg = dict(VARIANTS[variant])
    cfg.update(overrides)
    v2.set_seeds(seed)
    model_cls = nn_features.build_v2_model(hidden=hidden, clamp_cp=CORRECTION_CLAMP)
    model = model_cls()
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    huber = nn.SmoothL1Loss(beta=50.0 / OUTPUT_SCALE, reduction="none")
    bce = nn.BCELoss(reduction="none")

    t_indices = torch.from_numpy(enc["indices"])
    t_mask = torch.from_numpy(enc["mask"])
    t_aux = torch.from_numpy(enc["aux"])
    t_phase = torch.from_numpy(enc["phase"])
    t_units = torch.from_numpy(enc["phase_units"])
    t_base = torch.from_numpy(enc["base"])
    t_sf = torch.from_numpy(enc["sf"])
    t_target = torch.from_numpy(enc["target"])
    t_regret = torch.from_numpy(enc["regret"].astype(np.float32))

    pad_rows = torch.from_numpy(pad["rows"])
    pad_mask = torch.from_numpy(pad["mask"])

    train_ids = split_groups["train"]
    val_ids = split_groups["validation"]
    offsets = enc["offsets"]
    all_pairs = v2.build_all_pairs(enc)
    poff = all_pairs["offsets"]
    scratch = np.zeros(enc["base"].shape[0], dtype=np.int64)
    history: list[dict[str, Any]] = []
    best_score = -1e18
    best_preserved = -1.0
    any_eligible = False
    best_state: dict[str, Any] | None = None
    best_epoch = -1
    rng = np.random.default_rng(seed)
    started = time.perf_counter()

    def corrections_for(rows: np.ndarray) -> np.ndarray:
        model.eval()
        out = np.zeros(len(rows), dtype=np.float64)
        with torch.no_grad():
            for start in range(0, len(rows), 8192):
                chunk = rows[start : start + 8192]
                t = torch.from_numpy(chunk)
                residual, conf = model.raw(t_indices[t], t_mask[t], t_aux[t], t_phase[t])
                residual = torch.clamp(
                    residual * OUTPUT_SCALE, -CORRECTION_CLAMP, CORRECTION_CLAMP
                )
                if cfg["gate"]:
                    residual = residual * conf
                value = residual.numpy().astype(np.float64)
                floor = float(cfg.get("conf_min", 0.0))
                if cfg["gate"] and floor > 0.0:
                    value = np.where(conf.numpy() < floor, 0.0, value)
                if cfg["phase_gate"]:
                    value = np.where(t_units[t].numpy() <= v2.PHASE_GATE_MAX, value, 0.0)
                gain = np.trunc(value)
                if relative:
                    # The deployed correction is trunc(base * gain / 1024): the gain is
                    # truncated to an integer first, exactly as the agent does, and only
                    # then scaled, so selection sees the shipped arithmetic.
                    out[start : start + len(chunk)] = v3r.apply_relative(
                        enc["base"][chunk].astype(np.float64), gain, CORRECTION_CLAMP
                    )
                else:
                    out[start : start + len(chunk)] = gain
        return out

    val_rows = np.concatenate([np.arange(offsets[g], offsets[g + 1]) for g in val_ids])

    for epoch in range(epochs):
        model.train()
        order = rng.permutation(train_ids)
        totals = {"value": 0.0, "rank": 0.0, "list": 0.0, "top": 0.0,
                  "anchor": 0.0, "draw": 0.0, "conf": 0.0, "quiet": 0.0, "zero": 0.0}
        batches = 0
        for start in range(0, len(order), batch_groups):
            gids = order[start : start + batch_groups]
            rows = np.concatenate([np.arange(offsets[g], offsets[g + 1]) for g in gids])
            pair_rows = np.concatenate([np.arange(poff[g], poff[g + 1]) for g in gids])
            if len(pair_rows) == 0:
                continue
            scratch[rows] = np.arange(len(rows), dtype=np.int64)
            pb = torch.from_numpy(scratch[all_pairs["better"][pair_rows]])
            pw = torch.from_numpy(scratch[all_pairs["worse"][pair_rows]])
            anchor_mask = torch.from_numpy(all_pairs["anchor"][pair_rows])

            t = torch.from_numpy(rows)
            residual, conf = model.raw(t_indices[t], t_mask[t], t_aux[t], t_phase[t])
            bounded = torch.clamp(residual, -1.0, 1.0)  # OUTPUT_SCALE units
            correction = bounded * conf if cfg["gate"] else bounded
            if cfg["phase_gate"]:
                correction = correction * (t_units[t] <= v2.PHASE_GATE_MAX).float()

            base = t_base[t]
            sf = t_sf[t]
            target = torch.clamp(t_target[t], -CORRECTION_CLAMP, CORRECTION_CLAMP) / OUTPUT_SCALE
            if relative:
                # correction * OUTPUT_SCALE is the same bounded, gated gain the additive
                # form would deploy; here it scales the control's own evaluation instead
                # of being added to it.
                applied = v3r.relative_torch(
                    base, correction * OUTPUT_SCALE, CORRECTION_CLAMP
                )
            else:
                applied = correction * OUTPUT_SCALE
            deployed = base + applied

            # -- 1. value (calibration only, small weight)
            loss_value = huber(applied / OUTPUT_SCALE, target).mean()

            # -- 2. pairwise ranking, V2's term, regret-scaled margins
            gap = torch.from_numpy(all_pairs["gap"][pair_rows])
            violation = torch.relu(0.25 * gap - (deployed[pw] - deployed[pb]))

            # -- 3. anchor: eroding a gap the control already had right costs more than
            #       fixing a broken one gains. This is what V2 measured as the effective
            #       preservation mechanism.
            ctrl_gap = torch.from_numpy(all_pairs["ctrl_gap"][pair_rows])
            erosion = torch.relu(0.5 * torch.clamp(ctrl_gap, min=0.0) - (deployed[pw] - deployed[pb]))
            loss_anchor = (
                (erosion * anchor_mask).sum() / anchor_mask.sum()
                if anchor_mask.sum() > 0
                else torch.zeros(())
            )
            free = 1.0 - anchor_mask
            loss_rank = (
                (violation * free).sum() / free.sum() if free.sum() > 0 else torch.zeros(())
            )

            # -- 4 and 5. the two group-level objectives. A group is one decision, so
            #    both act on the padded (G, K) view rather than on pairs.
            gsel = torch.from_numpy(gids)
            grows = pad_rows[gsel]                       # (G, K) global child ids
            gmask = pad_mask[gsel]                       # (G, K) 1 where a real child
            local = torch.from_numpy(scratch[grows.numpy()])
            gdep = deployed[local]                       # (G, K) deployed cp
            gregret = t_regret[grows]                    # (G, K) teacher regret

            neg_inf = torch.finfo(torch.float32).min
            # The engine minimises the child's evaluation (it is the opponent's score),
            # so the preference score is the negated deployment.
            student = torch.where(gmask > 0, -gdep / STUDENT_TEMP_CP, torch.full_like(gdep, neg_inf))
            teacher = torch.where(
                gmask > 0, -gregret / TEACHER_TEMP_CP, torch.full_like(gregret, neg_inf)
            )
            log_student = torch.log_softmax(student, dim=1)
            p_teacher = torch.softmax(teacher, dim=1)
            loss_list = -(p_teacher * log_student * gmask).sum(dim=1).mean()

            # Regret-weighted top move: the hard label is the teacher's best child, and
            # each group's weight is the value the control currently throws away there.
            best_idx = torch.argmin(
                torch.where(gmask > 0, gregret, torch.full_like(gregret, 1e9)), dim=1
            )
            ctrl_pick = torch.argmin(
                torch.where(gmask > 0, gdep.detach(), torch.full_like(gdep, 1e9)), dim=1
            )
            ctrl_regret_now = gregret.gather(1, ctrl_pick.unsqueeze(1)).squeeze(1)
            weight = torch.clamp(ctrl_regret_now, 0.0, CORRECTION_CLAMP) / CORRECTION_CLAMP
            ce = -log_student.gather(1, best_idx.unsqueeze(1)).squeeze(1)
            loss_top = (ce * weight).sum() / torch.clamp(weight.sum(), min=1.0)

            # -- 6. draw preservation
            drawish = (torch.abs(sf) <= v2.DRAW_BAND_CP).float()
            allow = torch.maximum(torch.abs(base), torch.abs(sf))
            excess = torch.relu(torch.abs(deployed) - allow)
            loss_draw = (
                (excess * drawish).sum() / drawish.sum()
                if drawish.sum() > 0
                else torch.zeros(())
            )

            # -- 7. near-zero sign and order defence. In a position the teacher calls
            #    equal, any movement across zero is penalised outright rather than only
            #    when it happens to exceed the control's own magnitude.
            near_zero = (torch.abs(sf) <= v3m.NEAR_ZERO_CP).float()
            crossed = torch.relu(-torch.sign(base) * deployed)
            loss_zero = (
                (crossed * near_zero).sum() / near_zero.sum()
                if near_zero.sum() > 0
                else torch.zeros(())
            )

            # -- 8. trust head. The target is whether applying the raw correction would
            #    improve the *group's ordering*, not whether it shrinks this position's
            #    error: a head trained on residual magnitude is the thing V2 showed does
            #    not help.
            if cfg["gate"]:
                with torch.no_grad():
                    full = bounded.detach() * OUTPUT_SCALE
                    if relative:
                        full = v3r.relative_torch(base, full, CORRECTION_CLAMP)
                    ungated = base + full
                    ung = ungated[local]
                    pick_before = torch.argmin(
                        torch.where(gmask > 0, gdep.detach(), torch.full_like(gdep, 1e9)), dim=1
                    )
                    pick_after = torch.argmin(
                        torch.where(gmask > 0, ung, torch.full_like(ung, 1e9)), dim=1
                    )
                    r_before = gregret.gather(1, pick_before.unsqueeze(1)).squeeze(1)
                    r_after = gregret.gather(1, pick_after.unsqueeze(1)).squeeze(1)
                    improves = (r_after <= r_before).float()          # (G,)
                    per_child = improves.unsqueeze(1).expand_as(gmask)
                    target_conf = torch.zeros_like(base)
                    target_conf.index_put_(
                        (local.reshape(-1),), per_child.reshape(-1), accumulate=False
                    )
                loss_conf = bce(conf.clamp(1e-6, 1 - 1e-6), target_conf).mean()
            else:
                loss_conf = torch.zeros(())

            # -- 9. magnitude restraint, the dial V2 measured as most effective
            loss_quiet = torch.abs(applied / OUTPUT_SCALE).mean()

            loss = (
                cfg["w_value"] * loss_value
                + cfg["w_rank"] * loss_rank / OUTPUT_SCALE
                + cfg["w_list"] * loss_list
                + cfg["w_top"] * loss_top
                + cfg["w_anchor"] * loss_anchor / OUTPUT_SCALE
                + cfg["w_draw"] * loss_draw / OUTPUT_SCALE
                + cfg["w_zero"] * loss_zero / OUTPUT_SCALE
                + cfg["w_conf"] * loss_conf
                + cfg["w_quiet"] * loss_quiet
            )

            opt.zero_grad()
            loss.backward()
            opt.step()
            model.clip_weights()

            totals["value"] += float(loss_value.detach())
            totals["rank"] += float(loss_rank.detach())
            totals["list"] += float(loss_list.detach())
            totals["top"] += float(loss_top.detach())
            totals["anchor"] += float(loss_anchor.detach())
            totals["draw"] += float(loss_draw.detach())
            totals["conf"] += float(loss_conf.detach())
            totals["quiet"] += float(loss_quiet.detach())
            totals["zero"] += float(loss_zero.detach())
            batches += 1

        corr = np.zeros(enc["base"].shape[0], dtype=np.float64)
        corr[val_rows] = corrections_for(val_rows)
        gm = v3m.ranking_metrics(corr, enc, val_ids)
        vm = v3m.value_metrics(corr, enc, val_rows)
        record = {
            "epoch": epoch,
            "train_loss": {k: round(v / max(1, batches), 6) for k, v in totals.items()},
            **{k: round(v, 4) for k, v in gm.items()},
            **{k: round(v, 4) for k, v in vm.items()},
        }
        record["composite"] = round(v3m.composite({**gm, **vm}), 4)
        history.append(record)
        print(
            f"  [{variant} h{hidden} s{seed}] ep {epoch:2d} "
            f"comp={record['composite']:8.3f} "
            f"pres={gm['preserved_rate']:.4f} "
            f"top+={gm['top_move_accuracy_gain']:+.4f} "
            f"regret={gm['regret_reduction_cp']:+7.2f} "
            f"p99harm={gm['p99_harmful_regret_cp']:6.1f} "
            f"zflip={gm['near_zero_sign_flip_rate']:.4f} "
            f"|corr|={vm['mean_abs_correction']:6.2f}"
            f"{'  ELIGIBLE' if gm['preserved_rate'] >= PRESERVE_FLOOR else ''}",
            flush=True,
        )

        eligible = gm["preserved_rate"] >= PRESERVE_FLOOR
        record["eligible"] = eligible
        if eligible:
            if not any_eligible or record["composite"] > best_score:
                any_eligible = True
                best_score = record["composite"]
                best_epoch = epoch
                best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
        elif not any_eligible and gm["preserved_rate"] > best_preserved:
            best_preserved = gm["preserved_rate"]
            best_score = record["composite"]
            best_epoch = epoch
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}

    assert best_state is not None
    model.load_state_dict(best_state)
    q = v2.quantize(model, cfg["gate"], cfg["phase_gate"], float(cfg.get("conf_min", 0.0)))

    outdir.mkdir(parents=True, exist_ok=True)
    np.savez(
        outdir / "quantized.npz",
        embed=q["embed_q"],
        aux_w=q["aux_w_q"],
        aux_b=q["aux_b_q"],
        mg=q["mg_q"],
        eg=q["eg_q"],
        conf=q["conf_q"],
        scales=np.array([q["mg_scale"], q["eg_scale"], q["conf_scale"]], dtype=np.float64),
        biases=np.array([q["mg_bias"], q["eg_bias"], q["conf_bias"]], dtype=np.float64),
        flags=np.array(
            [int(q["gate"]), int(q["phase_gate"]), v2.PHASE_GATE_MAX,
             round(float(cfg.get("conf_min", 0.0)) * 1000), int(relative)],
            dtype=np.int64,
        ),
    )
    import torch

    torch.save(best_state, outdir / "float.pt")

    return {
        "variant": variant,
        "form": "relative" if relative else "additive",
        "hidden": hidden,
        "seed": seed,
        "config": cfg,
        "epochs_run": epochs,
        "best_epoch": best_epoch,
        "best_composite": best_score,
        "preserve_floor": PRESERVE_FLOOR,
        "eligible": any_eligible,
        "best_preserved_rate": max(h["preserved_rate"] for h in history),
        "train_seconds": round(time.perf_counter() - started, 1),
        "parameters": nn_features.parameter_count(hidden),
        "history": history,
        "quantized": str((outdir / "quantized.npz").relative_to(REPO)),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", default="RAP")
    ap.add_argument("--hidden", default="32")
    ap.add_argument("--seed", type=int, default=20260910)
    ap.add_argument("--epochs", type=int, default=10)
    ap.add_argument("--batch-groups", type=int, default=256)
    ap.add_argument("--lr", type=float, default=2e-3)
    ap.add_argument("--tag", default="v3")
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--relative", action="store_true", help="scale the correction by the control's own evaluation")
    ap.add_argument(
        "--override",
        action="append",
        default=[],
        help="loss coefficient override, e.g. w_quiet=0.6",
    )
    args = ap.parse_args()

    import torch

    torch.set_num_threads(args.threads)

    overrides: dict[str, float] = {}
    for item in args.override:
        key, _, value = item.partition("=")
        overrides[key] = float(value)

    groups = v2.load_groups()
    # Gate 6 is enforced here, not merely reported. nnue_v3_overlap.py found 40 keys
    # that were a parent in one split and a child in another -- a small leak V2's
    # provenance check could not see, because it compared parent keys with parent keys
    # and child keys with child keys but never across. The groups on the less protected
    # side are dropped before anything is trained on them.
    quarantine_path = OUTROOT / "quarantine.json"
    if quarantine_path.exists():
        blocked = set(json.loads(quarantine_path.read_text(encoding="utf-8"))["group_ids"])
        before = len(groups)
        groups = [g for g in groups if g["group_id"] not in blocked]
        print(f"quarantine: dropped {before - len(groups)} leaking groups")
    else:
        print("WARNING: no quarantine list; run tests/nnue_v3_overlap.py first")
    print(f"{len(groups)} sibling groups")
    enc = v2.encode_groups(
        groups, REPO / "tests" / "results" / "nnue" / "v2" / "encoded.npz"
    )
    pad_cache = OUTROOT / "padded.npz"
    if pad_cache.exists():
        blob = np.load(pad_cache)
        pad = {k: blob[k] for k in blob.files}
        if pad["rows"].shape[0] != len(groups):
            pad = padded_groups(enc)
            OUTROOT.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(pad_cache, **pad)
    else:
        pad = padded_groups(enc)
        OUTROOT.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(pad_cache, **pad)
    print(f"padded group width K={int(pad['k'])}")

    splits = np.array([g["split"] for g in groups])
    split_groups = {
        name: np.nonzero(splits == name)[0] for name in ("train", "validation", "test")
    }
    print({k: len(v) for k, v in split_groups.items()})

    results = []
    for variant in args.variants.split(","):
        for hidden in [int(h) for h in args.hidden.split(",")]:
            name = f"{variant}_h{hidden}_s{args.seed}_{args.tag}"
            outdir = OUTROOT / name
            info = train_variant(
                variant, hidden, args.seed, enc, pad, split_groups,
                args.epochs, args.batch_groups, args.lr, outdir, overrides,
                args.relative,
            )
            info["name"] = name
            (outdir / "training.json").write_text(
                json.dumps(info, indent=2), encoding="utf-8"
            )
            results.append(info)
            print(
                f"  -> {name}: best epoch {info['best_epoch']} "
                f"composite {info['best_composite']:.3f} "
                f"eligible={info['eligible']} in {info['train_seconds']}s",
                flush=True,
            )

    summary = OUTROOT / f"training_summary_{args.tag}.json"
    summary.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"wrote {summary}")


if __name__ == "__main__":
    main()
