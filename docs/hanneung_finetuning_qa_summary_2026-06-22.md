# 한능검 문제 생성 파인튜닝 작업 정리

작성일: 2026-06-22

## 1. 목표

한능검 심화 기출 기반 데이터를 이용해 LLaMA-Factory WebUI에서 SFT/LoRA 파인튜닝을 진행하고, 한국사능력검정시험 스타일의 5지선다 문제를 생성하는 모델을 만드는 것이 목표였다.

최종적으로는 다음 형태의 출력을 기대했다.

```json
{
  "question": "문제 발문",
  "choices": [
    "① 선택지",
    "② 선택지",
    "③ 선택지",
    "④ 선택지",
    "⑤ 선택지"
  ],
  "answer": "①",
  "explanation": "정답은 ①이다. ..."
}
```

## 2. 데이터셋 검토와 구조 변경

처음에는 `source`, `target_score` 중심의 단순 구조를 사용하려 했으나, 한능검 문제 유형이 다양하고 `source`와 `output`이 너무 비슷하면 모델이 입력 문장을 그대로 반복할 수 있다는 우려가 있었다.

그래서 최종 학습용 구조를 `structured_source_v5`로 정리했다.

최종 입력 구조는 다음과 같다.

```json
{
  "source": {
    "material_clues": [],
    "answer_basis": [],
    "distractor_basis": []
  },
  "major_type": "역사 자료의 분석 및 해석",
  "minor_type": "자료 기반 시대·대상 추론",
  "target_score": 2,
  "difficulty_label": "보통",
  "difficulty_reason": "서로 다른 단서 2묶음을 연결하고, 식별한 대상의 특징·활동·시기를 비교하는 문항이다."
}
```

각 필드의 의미는 다음과 같다.

`material_clues`: 자료나 사료에서 드러나는 핵심 단서. 확실한 따옴표/키워드만 추출했다.

`answer_basis`: 정답을 도출하는 역사 근거. 원본 source 문장을 문장 단위로 분리했다.

`distractor_basis`: 오답 선택지나 오답 해설에 필요한 근거. 원본 explanation의 오답 설명에서 가져왔다.

`major_type`: 대유형.

`minor_type`: 소유형.

`target_score`: 한능검 배점. 1, 2, 3점.

`difficulty_label`: target_score 기준 난이도. 1점은 쉬움, 2점은 보통, 3점은 어려움.

`difficulty_reason`: 난이도 설계 기준을 짧은 문장으로 설명한 필드.

## 3. 제거한 필드

최종 v5에서는 다음 필드를 제거했다.

```text
id
exam_name
era
topic
historical_context
```

제거 이유는 다음과 같다.

`id`: 학습에 불필요한 식별자이며, 모델이 무의미한 패턴으로 학습할 수 있다.

`exam_name`: 문제 생성에 직접 필요하지 않다.

`era`: 자동 추정값에 `미분류`가 많아 노이즈가 컸다.

`topic`: `미분류`, `상황이`, `뉴스가`, `자료를` 같은 자동 추정 흔적이 많아 제거했다.

`historical_context`: 빈 배열이 많고 `answer_basis`와 겹쳐 제거했다.

## 4. 최종 파일

최종 학습용 파일은 바탕화면의 `test1` 폴더에 따로 보관했다.

```text
C:\Users\Playdata\Desktop\hanneung_47_66_dataset\test1
```

파일 3개:

```text
hanneung_train_structured_source_v5_llamafactory_alpaca.json
hanneung_valid_structured_source_v5_llamafactory_alpaca.json
llamafactory_dataset_info_structured_source_v5_snippet.json
```

검수 결과:

```text
원본 검수본: 946개
train: 852개
valid: 94개
총합: 946개

새로 생성된 문제/선택지/정답/해설: 없음
원본 output과 최종 output multiset 일치: true
누락 output: 0
추가 output: 0
중복 output: 0

schema 오류: 0
선택지 5개 오류: 0
정답 ①~⑤ 오류: 0
해설 정답 번호 불일치: 0
내부 보고서 문구: 0
era/topic 제거 완료
```

중요한 caveat:

```text
공식 PDF OCR 원문 100% 대조 데이터는 아님
CBT/기출 기반으로 정리한 데이터임
major_type/minor_type은 공식 원문 필드가 아니라 분류 태그임
material_clues는 비어 있는 샘플이 많지만 answer_basis는 전부 있음
```

## 5. RunPod 환경 선택

처음 RTX 4090을 권장했지만, 실제 RunPod 선택 과정에서 Blackwell 계열 GPU와 PyTorch 2.4.0 호환 문제가 있었다.

피해야 할 GPU:

```text
RTX PRO 4000 Blackwell
RTX PRO 4500 Blackwell
RTX PRO 5000 Blackwell
RTX PRO 6000 Blackwell
B200
GB200
```

최종 선택:

```text
GPU: A40 48GB
Template: Runpod Pytorch 2.4.0
Image: runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04
Container Disk: 80GB
Volume Disk: 80GB
HTTP Ports: 8888,7860
TCP Ports: 22
```

7860 포트는 LLaMA-Factory WebUI용이다.

WebUI 실행 명령:

```bash
cd /workspace/LLaMA-Factory
llamafactory-cli webui --host 0.0.0.0 --port 7860
```

`--host 0.0.0.0`이 있어야 RunPod 외부 접속이 가능하다.

## 6. LLaMA-Factory 설치와 데이터 등록

설치 명령:

```bash
cd /workspace
nvidia-smi
git clone https://github.com/hiyouga/LLaMA-Factory.git
cd LLaMA-Factory
pip install -U pip setuptools wheel hatchling
pip install -e ".[torch,metrics]"
```

데이터 업로드 위치:

```text
/workspace/LLaMA-Factory/data
```

`dataset_info.json` 등록 코드:

```bash
cd /workspace/LLaMA-Factory
python - <<'PY'
import json
from pathlib import Path

data_dir = Path("data")
info_path = data_dir / "dataset_info.json"
snippet_path = data_dir / "llamafactory_dataset_info_structured_source_v5_snippet.json"

info = json.loads(info_path.read_text(encoding="utf-8"))
snippet = json.loads(snippet_path.read_text(encoding="utf-8"))

info.update(snippet)
info_path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")

print("registered:", list(snippet.keys()))
PY
```

등록된 데이터셋 이름:

```text
hanneung_structured_source_v5_train
hanneung_structured_source_v5_valid
```

## 7. 1차 학습 파라미터

최종으로 사용한 설정:

```yaml
stage: sft
finetuning_type: lora
model_name_or_path: Qwen/Qwen2.5-7B-Instruct
template: qwen
dataset: hanneung_structured_source_v5_train
cutoff_len: 1024
learning_rate: 1e-4
num_train_epochs: 1
max_steps: 100
per_device_train_batch_size: 2
gradient_accumulation_steps: 4
lr_scheduler_type: linear
max_grad_norm: 1.0
logging_steps: 5
save_steps: 50
warmup_steps: 5
packing: false
enable_thinking: false
bf16: true
optim: adamw_8bit
weight_decay: 0.01
seed: 3407
lora_rank: 8
lora_alpha: 16
lora_dropout: 0.05
lora_target: q_proj,k_proj,v_proj,o_proj,gate_proj,up_proj,down_proj
```

처음에는 4bit QLoRA를 시도했으나, PyTorch 2.4.0과 bitsandbytes/transformers 조합에서 다음 오류가 발생했다.

```text
AttributeError: 'Qwen2ForCausalLM' object has no attribute 'set_submodule'
```

해결:

```text
Quantization bit: none
```

A40 48GB에서는 4bit 없이 LoRA 학습이 가능했다.

## 8. 학습 결과

학습은 성공적으로 종료되었다.

성공 로그:

```text
Training completed.
Saving model checkpoint to saves/Qwen2.5-7B-Instruct/lora/train_2026-06-22-11-09-13
```

저장 위치:

```text
/workspace/LLaMA-Factory/saves/Qwen2.5-7B-Instruct/lora/train_2026-06-22-11-09-13
```

체크포인트:

```text
checkpoint-50
checkpoint-100
```

훈련 요약:

```text
Num examples = 852
Total optimization steps = 100
Trainable params = 20,185,088
All params = 7,635,801,600
Trainable% = 0.2643
```

Loss 흐름:

```text
초반 loss: 약 0.71
후반 loss: 약 0.25~0.30
```

그래프상 loss는 정상적으로 하락했고, NaN이나 폭발은 없었다.

## 9. 테스트 결과

### 9.1 기본 개념형

진대법 테스트에서 JSON 구조는 어느 정도 잡혔다.

좋아진 점:

```text
한국어 출력
JSON 형식 출력
choices ①~⑤ 형식
answer "①" 형식
정답 판단 가능
```

문제점:

```text
발문이 어색함
선지가 입력 근거를 그대로 복사하는 경향
해설이 짧음
오답 선지가 자연스럽지 않음
```

프롬프트를 강하게 주면 개선되었지만, 선지 품질은 아직 부족했다.

### 9.2 사료형

국채 보상 운동 테스트에서 정답은 맞았지만 문항 품질은 부족했다.

문제점:

```text
"(가) 제시된 사료" 같은 어색한 발문
선택지에 "~을 언급한다" 반복
"븍나로드" 같은 용어 오타
해설에서 "관련된다" 반복
```

원인:

```text
실제 passage가 input에 없어서 (가)가 붕 뜸
material_clues만으로 사료형을 생성하려 하니 발문이 어색해짐
```

결론:

```text
사료형은 passage 필드가 필요함
RAG에서 가져온 실제 사료 본문을 input에 넣는 구조가 필요함
```

### 9.3 연대기형

대야성 함락, 나당 동맹, 황산벌 전투 순서 배열 테스트는 실패에 가까웠다.

문제점:

```text
선택지 중복
나당 동맹 연도 오류
발문이 "정리하세요"처럼 한능검답지 않음
선택지가 너무 길고 정답 단서를 노출
오답 구성 논리 약함
```

원인:

```text
연대기형은 source.answer_basis만으로는 부족함
events/year/order_answer 구조가 필요함
```

## 10. 핵심 결론

1차 파인튜닝은 기술적으로 성공했다.

```text
데이터 로딩 성공
토크나이징 성공
LoRA 학습 성공
체크포인트 저장 성공
JSON 형식 일부 학습 성공
```

하지만 하나의 공통 input schema로 모든 한능검 유형을 처리하기에는 부족했다.

문제 생성 품질 관점에서는 다음 한계가 확인되었다.

```text
사료형은 passage 없이 불안정
연대기형은 events/year/order_answer 없이 불안정
선지 품질은 아직 약함
오답 구조가 덩어리라 자연스러운 오답 생성이 어려움
유형별 문법을 모델이 충분히 분리하지 못함
```

## 11. 다음 방향: 대유형별 데이터셋 분리

다음 단계는 대유형 6개로 데이터셋을 나누는 것이다.

대유형:

```text
역사 자료의 분석 및 해석
연대기의 파악
역사 탐구의 설계 및 수행
역사 상황 및 쟁점의 인식
결론의 도출 및 평가
역사 지식의 이해
```

단순히 파일만 나누는 것이 아니라, input schema도 유형별로 달라져야 한다.

### 11.1 사료형/자료형

```json
{
  "question_type": "source_analysis",
  "source": {
    "passage": "실제 사료 또는 RAG로 가져온 자료문",
    "material_clues": [],
    "answer_basis": [],
    "distractor_basis": []
  },
  "major_type": "역사 자료의 분석 및 해석",
  "minor_type": "사료·문헌 해석",
  "target_score": 2
}
```

### 11.2 연대기형

```json
{
  "question_type": "chronology_order",
  "events": [
    {
      "label": "(가)",
      "event": "대야성 함락",
      "year": 642,
      "description": "백제 윤충이 신라의 대야성을 함락하였다."
    },
    {
      "label": "(나)",
      "event": "나당 동맹 체결",
      "year": 648,
      "description": "김춘추가 당과 군사 동맹을 맺었다."
    },
    {
      "label": "(다)",
      "event": "황산벌 전투",
      "year": 660,
      "description": "계백이 이끄는 백제군이 나당 연합군에 맞서 싸웠다."
    }
  ],
  "order_answer": ["(가)", "(나)", "(다)"],
  "target_score": 3
}
```

기대 출력:

```json
{
  "question": "(가)~(다)를 일어난 순서대로 옳게 나열한 것은?",
  "choices": [
    "① (가) - (나) - (다)",
    "② (가) - (다) - (나)",
    "③ (나) - (가) - (다)",
    "④ (나) - (다) - (가)",
    "⑤ (다) - (가) - (나)"
  ],
  "answer": "①",
  "explanation": "정답은 ①이다. (가)는 642년 대야성 함락, (나)는 648년 나당 동맹 체결, (다)는 660년 황산벌 전투이다. 따라서 순서는 (가) - (나) - (다)이다."
}
```

### 11.3 지식/개념형

```json
{
  "question_type": "knowledge_check",
  "answer_focus": "진대법",
  "answer_basis": [],
  "distractors": [
    {
      "term": "의창",
      "reason": "고려 성종"
    },
    {
      "term": "상평창",
      "reason": "물가 조절"
    }
  ],
  "target_score": 1
}
```

## 12. 다음 작업 제안

다음 작업은 v6 데이터셋 설계다.

우선순위:

```text
1. 대유형별로 기존 946개를 분리
2. 각 대유형별 input schema 설계
3. 연대기형부터 events/year/order_answer 구조로 재가공
4. 사료형은 passage 필드를 추가하는 방향으로 RAG 연결 전제 설계
5. 지식형은 answer_focus와 distractors를 명시
6. v6로 다시 SFT 학습
```

1차 모델은 버리는 것이 아니라 기준선으로 보관한다.

1차 모델의 역할:

```text
공통 JSON 형식 학습 기준선
LLaMA-Factory/RunPod 파이프라인 검증
파라미터 기준선
유형별 한계 확인용 baseline
```

