# Eval Splits With Core v1

## Purpose
- Add `core_concept` to model input for topic/topic_train experiments.
- Keep the original `text` field unchanged for baseline comparison.
- New input field: `text_with_core`.

## Files
| split | file | rows | missing core_concept |
|---|---:|---:|---:|
| split_era_topic_train_stratified_v1 | train.json | 1199 | 0 |
| split_era_topic_train_stratified_v1 | test.json | 401 | 0 |
| split_time_v1 | train.json | 1200 | 0 |
| split_time_v1 | test.json | 400 | 0 |

## RunPod Use
- Upload this folder as `/workspace/common/eval_splits_with_core_v1`.
- In the core experiment notebook, use `INPUT_TEXT_FIELD = 'text_with_core'`.
- Recommended first target: `TARGET = 'topic_train'`.
