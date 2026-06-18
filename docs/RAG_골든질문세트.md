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
etl/history/embedding/golden_questions.jsonl
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
| GQ035 | 을사늑약이 왜 중요한 사건이야 | concept | 근대 | historical_overview | 을사늑약, 외교권, 통감부 |
| GQ036 | 3.1 운동 배경과 영향 알려줘 | summary | 근대 | historical_overview | 3.1 운동, 민족 자결주의, 대한민국 임시정부 |
| GQ037 | 대한민국 임시정부의 활동 설명해줘 | concept | 근대 | historical_overview | 임시정부, 상하이, 한인 애국단 |
| GQ038 | 광복 이후 좌우 합작 운동 설명해줘 | concept | 현대 | historical_overview | 좌우 합작, 여운형, 김규식 |
| GQ039 | 6월 민주 항쟁이 뭐야 | concept | 현대 | historical_overview | 6월 민주 항쟁, 직선제, 민주화 |
| GQ040 | 한국사능력검정시험에서 조선 전기 정치는 어떻게 외우면 좋아? | study_tip | 조선 전기 | historical_overview | 조선 전기, 태종, 세종, 성종, 경국대전 |

## 평가 메모

초기 평가는 엄격한 단일 정답보다 `expected_keywords`가 상위 5개 결과의 제목, 본문, metadata에 들어오는지를 본다.

이미지 질문은 `source_type=image_material`이 상위 결과에 포함되고 `thumbnail_url` 또는 `original_image_url`이 있으면 1차 성공으로 본다.
