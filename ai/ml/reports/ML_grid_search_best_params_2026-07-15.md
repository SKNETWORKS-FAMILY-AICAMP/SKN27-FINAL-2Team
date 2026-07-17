# ML Grid Search Best Params - 2026-07-15

## Source Folders

Inner output file names were not used as the target source of truth because several files still used `topic_train` in their names. The target was identified from the outer folder name.

| Target | Folder |
| --- | --- |
| `era` | `C:/Users/Playdata/Downloads/klue_grid_search_v2_final(era)/` |
| `topic_train` | `C:/Users/Playdata/Downloads/klue_grid_search_v2_final(topic_train)/` |
| `topic` | `C:/Users/Playdata/Downloads/klue_grid_search_v2_final(topic)/` |

## Best Params

Best params were selected by `f1_macro_mean`, then checked against the final result JSON params.

| Target | max_length | learning_rate | batch_size | patience | final_epochs | CV Macro F1 Mean | CV Accuracy Mean |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `era` | 512 | 5e-6 | 16 | 3 | 17 | 0.9087 | 0.9208 |
| `topic_train` | 512 | 1e-5 | 16 | 3 | 5 | 0.8028 | 0.8165 |
| `topic` | 512 | 1e-5 | 8 | 3 | 6 | 0.7366 | 0.8182 |

## Final Test Results

| Target | Split | Accuracy | Macro F1 | Weighted F1 |
| --- | --- | ---: | ---: | ---: |
| `topic_train` | stratified | 0.8603 | 0.8517 | 0.8603 |
| `topic_train` | split_time_v1 | 0.8075 | 0.8050 | 0.8063 |
| `era` | stratified | 0.9252 | 0.9120 | 0.9250 |
| `era` | split_time_v1 | 0.9325 | 0.9187 | 0.9328 |
| `topic` | stratified | 0.8130 | 0.6830 | 0.8182 |
| `topic` | split_time_v1 | 0.7850 | 0.6862 | 0.7751 |

## Notebook Updated

The trend prediction notebook now uses target-specific params through `TARGET_CONFIG`.

- Generator: `ai/ml/make_runpod_trend_predict_era_topic_v1_notebook.py`
- Notebook: `ai/ml/runpod_trend_predict_era_topic_v1.ipynb`

Current `TARGET_CONFIG`:

```python
TARGET_CONFIG = {
    'era': {
        'max_length': 512,
        'learning_rate': 5e-6,
        'batch_size': 16,
        'max_epochs': 17,
        'use_class_weight': True,
    },
    'topic_train': {
        'max_length': 512,
        'learning_rate': 1e-5,
        'batch_size': 16,
        'max_epochs': 5,
        'use_class_weight': True,
    },
    'topic': {
        'max_length': 512,
        'learning_rate': 1e-5,
        'batch_size': 8,
        'max_epochs': 6,
        'use_class_weight': True,
    },
}
```

## Next Run

Upload `runpod_trend_predict_era_topic_v1.ipynb` to RunPod and run it with:

- `/workspace/common/split_v2/full_features_v2.csv`
- GPU session only
- one notebook running at a time to avoid CUDA OOM
