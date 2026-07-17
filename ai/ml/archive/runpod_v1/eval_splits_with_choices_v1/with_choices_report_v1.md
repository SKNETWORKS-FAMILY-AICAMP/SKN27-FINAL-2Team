# Eval Splits With Choices v1

## Purpose
- Add visible answer-choice text to model input for topic/topic_train experiments.
- Keep the original `text` field unchanged for baseline comparison.
- Do not include `is_answer`, `answer_no`, `answer_choice`, or explanations in `text_with_choices`.

## Files
| split | file | rows | missing raw | missing choices |
|---|---:|---:|---:|---:|
| split_era_topic_train_stratified_v1 | train.json | 1199 | 0 | 0 |
| split_era_topic_train_stratified_v1 | test.json | 401 | 0 | 0 |
| split_time_v1 | train.json | 1200 | 0 | 0 |
| split_time_v1 | test.json | 400 | 0 | 0 |

## RunPod Use
- Upload this folder as `/workspace/common/eval_splits_with_choices_v1`.
- In the choice experiment notebook, use `INPUT_TEXT_FIELD = 'text_with_choices'`.
- Recommended first target: `TARGET = 'topic_train'`.
