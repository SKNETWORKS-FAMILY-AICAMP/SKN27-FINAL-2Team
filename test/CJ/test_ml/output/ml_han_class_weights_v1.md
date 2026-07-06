# ML_han v1 Class Weight

- 기준 파일: `test/CJ/test_ml/output/ml_han_features_v1.json`
- train: 47~70회
- test: 71~78회
- 적용 대상 라벨: `era`, `topic`, `question_type`
- 계산 공식: `전체 train 샘플 수 / (클래스 수 * 해당 클래스 샘플 수)`

## 사용 방법

- baseline 모델: 각 row의 `sample_weights[target]`를 학습 함수에 전달합니다.
- KLUE/RoBERTa: `class_weights[target]`를 라벨 id 순서대로 tensor로 만든 뒤 `CrossEntropyLoss(weight=...)`에 전달합니다.
- 평가는 Accuracy만 보지 말고 `Macro F1`, `Weighted F1`, `per-class F1`을 같이 봅니다.

## era

| 라벨 | train 건수 | 비율 | class weight | label id |
|---|---:|---:|---:|---:|
| 조선 | 379 | 31.6% | 0.316623 | 7 |
| 고려 | 174 | 14.5% | 0.689655 | 1 |
| 삼국 시대 | 134 | 11.2% | 0.895522 | 4 |
| 개항기 | 127 | 10.6% | 0.944882 | 0 |
| 현대 | 115 | 9.6% | 1.043478 | 9 |
| 일제 강점기 | 95 | 7.9% | 1.263158 | 6 |
| 남북국 시대 | 80 | 6.7% | 1.500000 | 3 |
| 초기 국가 | 52 | 4.3% | 2.307692 | 8 |
| 선사 시대 | 24 | 2.0% | 5.000000 | 5 |
| 고조선 | 20 | 1.7% | 6.000000 | 2 |

## topic

| 라벨 | train 건수 | 비율 | class weight | label id |
|---|---:|---:|---:|---:|
| 사건 | 340 | 28.3% | 0.352941 | 3 |
| 인물 | 304 | 25.3% | 0.394737 | 7 |
| 정치 | 192 | 16.0% | 0.625000 | 8 |
| 제도 | 136 | 11.3% | 0.882353 | 9 |
| 문화 | 132 | 11.0% | 0.909091 | 2 |
| 사회 | 29 | 2.4% | 4.137931 | 5 |
| 군사 | 18 | 1.5% | 6.666667 | 1 |
| 경제 | 17 | 1.4% | 7.058824 | 0 |
| 사상·종교 | 16 | 1.3% | 7.500000 | 4 |
| 외교 | 16 | 1.3% | 7.500000 | 6 |

## question_type

| 라벨 | train 건수 | 비율 | class weight | label id |
|---|---:|---:|---:|---:|
| 역사 자료의 분석 및 해석 | 729 | 60.8% | 0.329218 | 1 |
| 연대기의 파악 | 288 | 24.0% | 0.833333 | 4 |
| 역사 지식의 이해 | 94 | 7.8% | 2.553191 | 2 |
| 역사 탐구의 설계 및 수행 | 76 | 6.3% | 3.157895 | 3 |
| 결론의 도출 및 평가 | 13 | 1.1% | 18.461538 | 0 |

## PyTorch 적용 예시

```python
# target = 'question_type' 예시
label_to_id = assets[target]['label_to_id']
class_weights = assets[target]['class_weights']
weight_tensor = torch.tensor(
    [class_weights[label] for label, _ in sorted(label_to_id.items(), key=lambda x: x[1])],
    dtype=torch.float,
    device=device,
)
loss_fn = torch.nn.CrossEntropyLoss(weight=weight_tensor)
```
