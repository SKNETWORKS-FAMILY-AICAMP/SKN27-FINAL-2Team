# RAG 골든 질문 세트

## 목적

이 문서는 한국사 챗봇 RAG 검색 품질을 평가하기 위한 초기 골든 질문 세트이다.

평가 기준:

```text
- 상위 5개 검색 결과 안에 기대 키워드가 포함되는가
- 기대 시대 metadata가 맞는가
- 질문 유형에 맞는 source_type이 검색되는가
- 이미지 질문은 thumbnail_url 또는 original_image_url이 있는가
```

실행용 JSONL 파일:

```text
etl/preprocessing/history/embedding/golden_questions.jsonl
```

## 질문 세트

| id | query | intent | expected_era | expected_source_type | expected_keywords |
|---|---|---|---|---|---|
| GQ001 | 조선 전기 6조 직계제 설명해줘 | concept | 조선 전기 | historical_overview | 6조, 직계제, 태종, 의정부 |
| GQ002 | 조선 전기 정치 제도 정리해줘 | concept | 조선 전기 | historical_overview | 의정부, 6조, 경국대전, 집현전 |
| GQ003 | 태종과 세종의 통치 체제 차이 알려줘 | compare | 조선 전기 | historical_overview | 태종, 세종, 6조 직계제, 의정부 서사제 |
| GQ004 | 훈민정음 창제 배경 설명해줘 | concept | 조선 전기 | historical_overview | 훈민정음, 세종, 집현전, 문자 |
| GQ005 | 경국대전이 왜 중요한지 알려줘 | concept | 조선 전기 | historical_overview | 경국대전, 성종, 법전, 통치 체제 |
| GQ006 | 사림이 성장한 배경 설명해줘 | concept | 조선 전기 | historical_overview | 사림, 성종, 훈구, 향촌 |
| GQ007 | 붕당 정치가 생긴 이유가 뭐야 | concept | 조선 후기 | historical_overview | 붕당, 사림, 동인, 서인 |
| GQ008 | 임진왜란 이후 조선 사회 변화 정리해줘 | summary | 조선 후기 | historical_overview | 임진왜란, 비변사, 대동법, 신분제 |
| GQ009 | 대동법이 뭔지 쉽게 설명해줘 | concept | 조선 후기 | historical_overview | 대동법, 공납, 쌀, 광해군 |
| GQ010 | 영정법과 대동법 차이 알려줘 | compare | 조선 후기 | historical_overview | 영정법, 대동법, 전세, 공납 |
| GQ011 | 전시과 제도가 뭐야 | concept | 고려 시대 | historical_overview | 전시과, 고려, 토지, 관료 |
| GQ012 | 고려의 토지 제도 흐름 정리해줘 | summary | 고려 시대 | historical_overview | 역분전, 전시과, 녹과전, 과전법 |
| GQ013 | 문벌 귀족 사회의 특징 알려줘 | concept | 고려 시대 | historical_overview | 문벌 귀족, 음서, 공음전, 혼인 |
| GQ014 | 무신 정변이 일어난 배경 설명해줘 | concept | 고려 시대 | historical_overview | 무신 정변, 문신 우대, 정중부, 의종 |
| GQ015 | 권문세족과 신진 사대부 차이 알려줘 | compare | 고려 시대 | historical_overview | 권문세족, 신진 사대부, 성리학, 토지 |
| GQ016 | 고려 말 과전법이 왜 시행됐어 | concept | 고려 시대 | historical_overview | 과전법, 신진 사대부, 권문세족, 토지 개혁 |
| GQ017 | 고려 불교 정책 정리해줘 | summary | 고려 시대 | historical_overview | 불교, 광종, 의천, 지눌 |
| GQ018 | 팔만대장경 만든 이유 알려줘 | concept | 고려 시대 | historical_overview | 팔만대장경, 몽골, 불교, 대장경 |
| GQ019 | 삼국 통일 과정 정리해줘 | summary | 삼국 시대 | historical_overview | 신라, 백제, 고구려, 나당 연합 |
| GQ020 | 고구려의 전성기 왕과 업적 알려줘 | concept | 삼국 시대 | historical_overview | 광개토 대왕, 장수왕, 남진 정책 |
| GQ021 | 백제 문화의 특징 설명해줘 | concept | 삼국 시대 | historical_overview | 백제, 무령왕릉, 일본, 불교 |
| GQ022 | 신라 골품제가 뭔지 알려줘 | concept | 삼국 시대 | historical_overview | 골품제, 신라, 성골, 진골 |
| GQ023 | 발해가 고구려를 계승했다는 근거 알려줘 | evidence | 통일 신라와 발해 | historical_source | 발해, 고구려, 대조영, 계승 |
| GQ024 | 통일 신라의 민정문서가 보여주는 내용은 뭐야 | evidence | 통일 신라와 발해 | historical_source | 민정문서, 촌락, 인구, 토지 |
| GQ025 | 연천 전곡리 유적 사진 보여줘 | image | 선사 | image_material | 연천, 전곡리, 주먹도끼 |
| GQ026 | 구석기 시대 대표 유물 이미지 찾아줘 | image | 선사 | image_material | 구석기, 주먹도끼, 찍개 |
| GQ027 | 빗살무늬 토기 사진 자료 있어? | image | 선사 | image_material | 빗살무늬 토기, 신석기, 토기 |
| GQ028 | 고인돌 사진 보여줘 | image | 삼국 이전 | image_material | 고인돌, 청동기, 무덤 |
| GQ029 | 무령왕릉 관련 이미지 찾아줘 | image | 삼국 시대 | image_material | 무령왕릉, 백제, 공주 |
| GQ030 | 첨성대 사진과 설명 알려줘 | image | 삼국 시대 | image_material | 첨성대, 신라, 천문 |
| GQ031 | 고려청자 이미지 보여줘 | image | 고려 시대 | image_material | 고려청자, 청자, 상감 |
| GQ032 | 훈민정음 해례본 이미지 자료 찾아줘 | image | 조선 전기 | image_material | 훈민정음, 해례본, 세종 |
| GQ033 | 독립협회 활동 정리해줘 | concept | 근대 | historical_overview | 독립협회, 독립문, 만민공동회 |
| GQ034 | 갑오개혁 내용 요약해줘 | summary | 근대 | historical_overview | 갑오개혁, 군국기무처, 신분제 폐지 |
| GQ035 | 을사조약이 왜 중요한 사건이야 | concept | 근대 | historical_overview | 을사늑약, 외교권, 통감부 |
| GQ036 | 3.1 운동 배경과 영향 알려줘 | summary | 근대 | historical_overview | 3.1 운동, 민족 자결주의, 대한민국 임시정부 |
| GQ037 | 대한민국 임시정부의 활동 설명해줘 | concept | 근대 | historical_overview | 임시정부, 상하이, 한인 애국단 |
| GQ038 | 광복 이후 좌우 합작 운동 설명해줘 | concept | 현대 | historical_overview | 좌우 합작, 여운형, 김규식 |
| GQ039 | 6월 민주 항쟁이 뭐야 | concept | 현대 | historical_overview | 6월 민주 항쟁, 직선제, 민주화 |
| GQ040 | 한국사능력검정시험에서 조선 전기 정치는 어떻게 외우면 좋아? | study_tip | 조선 전기 | historical_overview | 조선 전기, 태종, 세종, 성종, 경국대전 |
| GQ041 | 세종대왕알려줘 | concept | 조선 전기 | historical_overview | 세종, 훈민정음, 집현전 |
| GQ042 | 장영실 업적 알려줘 | concept | 조선 전기 | historical_overview, image_material | 장영실, 측우기, 앙부일구 |
| GQ043 | 권율 장군에 대해서 알려줘 | concept | 조선 전기 | historical_overview | 권율, 행주대첩, 임진왜란 |
| GQ044 | 왕건의 고려 건국 과정 정리해줘 | concept | 고려 시대 | historical_overview | 왕건, 고려 건국, 후삼국 |
| GQ045 | 광종의 개혁 정치 알려줘 | concept | 고려 시대 | historical_overview | 광종, 노비안검법, 과거제 |
| GQ046 | 고려 성종의 통치 제도 설명해줘 | concept | 고려 시대 | historical_overview | 성종, 유교, 지방 제도 |
| GQ047 | 이성계가 조선을 세운 배경 알려줘 | concept | 고려 시대, 조선 전기 | historical_overview | 이성계, 위화도 회군, 조선 건국 |
| GQ048 | 실학의 역사적 의미 설명해줘 | concept | 조선 후기 | historical_overview | 실학, 중농학파, 중상학파 |
| GQ049 | 동학 농민 운동의 전개 과정 알려줘 | summary | 근대 | historical_overview | 동학 농민 운동, 전봉준, 고부, 집강소 |
| GQ050 | 일제 강점기 문화 통치 특징 정리해줘 | concept | 일제 강점기 | historical_overview | 문화 통치, 3.1 운동, 민족 분열 통치 |

## RAGAS 균형 보강 질문

초기 세트는 `concept` 비중이 높고 `summary`, `compare`, `evidence`가 적었다. 실제
서비스 RAGAS 평가가 특정 질문 유형에 치우치지 않도록 GQ051~GQ083을 추가했다.

| id | query | intent | expected_era | expected_source_type |
|---|---|---|---|---|
| GQ051 | 조선 건국 과정을 핵심만 요약해줘 | summary | 고려 시대, 조선 전기 | historical_overview |
| GQ052 | 세종 시대의 문화 정책을 요약해줘 | summary | 조선 전기 | historical_overview |
| GQ053 | 임진왜란 전개 과정을 요약해줘 | summary | 조선 후기 | historical_overview |
| GQ054 | 고려 무신 정권의 흐름을 요약해줘 | summary | 고려 시대 | historical_overview |
| GQ055 | 삼국의 중앙 집권화 과정을 요약해줘 | summary | 삼국 시대 | historical_overview |
| GQ056 | 갑오개혁의 주요 내용을 요약해줘 | summary | 근대 | historical_overview |
| GQ057 | 광복 이후 민주화 운동 흐름을 요약해줘 | summary | 현대 | historical_overview |
| GQ058 | 발해의 건국과 발전 과정을 요약해줘 | summary | 통일 신라와 발해 | historical_overview |
| GQ059 | 의정부서사제와 6조 직계제의 차이 알려줘 | compare | 조선 전기 | historical_overview |
| GQ060 | 훈구와 사림의 차이 알려줘 | compare | 조선 전기 | historical_overview |
| GQ061 | 전시과와 과전법의 차이 알려줘 | compare | 고려 시대 | historical_overview |
| GQ062 | 문벌 귀족 사회와 무신 정권의 차이 알려줘 | compare | 고려 시대 | historical_overview |
| GQ063 | 광종과 성종의 개혁 정치를 비교해줘 | compare | 고려 시대 | historical_overview |
| GQ064 | 고구려와 백제 문화의 차이 알려줘 | compare | 삼국 시대 | historical_overview |
| GQ065 | 신라와 발해의 통치 체제를 비교해줘 | compare | 통일 신라와 발해 | historical_overview |
| GQ066 | 삼국 통일과 후삼국 통일의 차이 알려줘 | compare | 삼국 시대, 고려 시대 | historical_overview |
| GQ067 | 갑오개혁과 광무개혁의 차이 알려줘 | compare | 근대 | historical_overview |
| GQ068 | 3.1 운동과 6월 민주 항쟁의 공통점과 차이 알려줘 | compare | 근대, 현대 | historical_overview |
| GQ069 | 독립협회와 대한자강회의 차이 알려줘 | compare | 근대 | historical_overview |
| GQ070 | 정도전과 정몽주의 정치 노선 차이 알려줘 | compare | 고려 시대, 조선 전기 | historical_overview |
| GQ071 | 위화도 회군이 조선 건국의 배경이라는 근거 알려줘 | evidence | 고려 시대, 조선 전기 | historical_source |
| GQ072 | 훈민정음이 백성을 위한 문자라는 근거 알려줘 | evidence | 조선 전기 | historical_source |
| GQ073 | 팔만대장경 제작 목적의 근거 알려줘 | evidence | 고려 시대 | historical_source |
| GQ074 | 무령왕릉이 백제의 대외 교류를 보여주는 근거 알려줘 | evidence | 삼국 시대 | historical_source |
| GQ075 | 광개토대왕릉비가 고구려 전성기의 근거가 되는 이유 알려줘 | evidence | 삼국 시대 | historical_source |
| GQ076 | 민정문서가 통일 신라 촌락의 모습을 보여주는 근거 알려줘 | evidence | 통일 신라와 발해 | historical_source |
| GQ077 | 발해가 고구려를 계승했다는 추가 근거 알려줘 | evidence | 통일 신라와 발해 | historical_source |
| GQ078 | 독도 영유권을 보여주는 역사 자료의 근거 알려줘 | evidence | 조선 후기 | historical_source |
| GQ079 | 을사늑약이 국권 피탈의 과정이라는 근거 알려줘 | evidence | 근대 | historical_source |
| GQ080 | 3.1 운동이 민족 운동의 전환점이라는 근거 알려줘 | evidence | 근대 | historical_source |
| GQ081 | 6월 민주 항쟁이 민주화의 전환점이라는 근거 알려줘 | evidence | 현대 | historical_source |
| GQ082 | 동학 농민 운동이 사회 개혁 요구였다는 근거 알려줘 | evidence | 근대 | historical_source |
| GQ083 | 경국대전이 조선 통치 체제의 근거가 되는 이유 알려줘 | evidence | 조선 전기 | historical_source |

## RAGAS 표본 구성

`evaluate_service_metrics.py --ragas`는 이미지·학습 팁 질문을 제외하고 아래 네 유형을
라운드로빈 방식으로 선택한다. 최종 평가에서 `--ragas-limit 60`을 사용하면 유형별 15건씩,
총 60건이 평가된다. `concept` 원본 질문 29건은 유지하되, 균형 평가에는 15건만 사용한다.

| 평가 유형 | 전체 보유 수 | RAGAS 최종 표본 수 |
|---|---:|---:|
| concept | 29 | 15 |
| summary | 15 | 15 |
| compare | 15 | 15 |
| evidence | 15 | 15 |

## 평가 메모

초기 평가는 엄격한 단일 정답보다 `expected_keywords`가 상위 5개 결과의 제목, 본문, metadata에 들어오는지를 본다.

이미지 질문은 `source_type=image_material`이 상위 결과에 포함되고 `thumbnail_url` 또는 `original_image_url`이 있으면 1차 성공으로 본다.
