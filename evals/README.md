# Evaluation workflow

1. Import a representative set with album/occasion context.
2. Run the worker and review all five fields, including explicit unknowns.
3. Export with `photo-agent evals --output data/evals/reviewed.jsonl`.
4. Change the provider/model or a versioned question file and import the same set again.
5. Compare predictions against a frozen, independently reviewed label set.

The built-in report summarizes reviewed runs; it does not perform a held-out
experiment or tune prompts automatically. It reports accuracy on known human labels
and prediction abstention rate. Review selection can bias these numbers. Demo runs are
included and should be excluded from model comparisons. Reviewers see predictions,
so use an independently annotated set for less biased quality estimates.

JSONL contains asset hashes, job IDs, snapshotted experiment configuration, raw provider
results, and the latest full human annotation. No image bytes or API keys are exported.
Album context and provider responses may contain personal information; keep exports private.
Use album/event groups and perceptual duplicate clusters to define train/dev/test splits
before tuning; repeated runs and visually similar photos must stay in the same split.
Exact file hashes alone cannot identify re-encoded or near-duplicate images.
