const fs = require("fs");
const path = require("path");

const ROOT_DIR = path.resolve(__dirname, "../../..");
const ML_DIR = path.join(ROOT_DIR, "test", "CJ", "test_ml");
const DOCS_DIR = path.join(ROOT_DIR, "test", "CJ", "test_docs");
const INPUT_JSON = path.join(ML_DIR, "ML_han_v1.json");
const REFERENCE_JSON = path.join(ML_DIR, "era_reference.json");
const ERA_OVERRIDES_JSON = path.join(ML_DIR, "ml_keyword_era_overrides.json");
const PERSON_JSON = path.join(DOCS_DIR, "3. 참고 자료", "시대별_인물_정리_v2_1.json");
const OUT_DIR = path.join(ML_DIR, "output");
const OUTPUT_JSON = path.join(OUT_DIR, "ml_han_features_v1.json");
const OUTPUT_CSV = path.join(OUT_DIR, "ml_han_features_v1.csv");
const REPORT_JSON = path.join(OUT_DIR, "ml_han_features_v1_report.json");

const ERA_VALUES = [
  "선사 시대",
  "고조선",
  "초기 국가",
  "삼국 시대",
  "남북국 시대",
  "고려",
  "조선",
  "개항기",
  "일제 강점기",
  "현대",
];

const TOPIC_VALUES = [
  "정치",
  "경제",
  "사회",
  "문화",
  "인물",
  "군사",
  "외교",
  "사상·종교",
  "제도",
  "사건",
];

const QUESTION_TYPES = [
  "역사 지식의 이해",
  "연대기의 파악",
  "역사 상황 및 쟁점의 인식",
  "역사 자료의 분석 및 해석",
  "역사 탐구의 설계 및 수행",
  "결론의 도출 및 평가",
];

const QUESTION_SUBTYPES = [
  "기본 사실·개념 확인",
  "자료 기반 시대·대상 추론",
  "사건·자료 순서 배열",
  "연표·흐름 빈칸",
  "전후 시기 판단",
  "지도·지역 위치 판단",
  "시각 자료 해석",
  "제도·기관·정책 기능 이해",
  "탐구 주제·활동 선정",
  "자료 수집·검색 방법",
  "의의·영향·결과 평가",
  "비교·공통점 도출",
  "보기 조합 판단",
];

const TOPIC_TYPE_TO_TOPIC = {
  "인물": "인물",
  "제도": "제도",
  "사건": "사건",
  "문화": "문화",
  "문화유산": "문화",
  "집단": "정치",
  "매체": "문화",
};

const QUESTION_TASK_TO_TYPE = {
  "order": "연대기의 파악",
  "timeline_position": "연대기의 파악",
  "period_between": "연대기의 파악",
  "map_location": "역사 자료의 분석 및 해석",
  "multi_select_combo": "역사 자료의 분석 및 해석",
  "negative_select": "역사 상황 및 쟁점의 인식",
  "standard_select": "역사 지식의 이해",
};

const ERA_ALIAS = {
  "조선 전기": "조선",
  "조선 후기": "조선",
};

const MANUAL_ERA_OVERRIDES = {
  "구석기": "선사 시대",
  "신석기": "선사 시대",
  "청동기": "선사 시대",
  "고조선": "고조선",
  "우거왕": "고조선",
  "부여": "초기 국가",
  "옥저": "초기 국가",
  "동예": "초기 국가",
  "삼한": "초기 국가",
  "백제": "삼국 시대",
  "고구려": "삼국 시대",
  "신라": "삼국 시대",
  "가야": "삼국 시대",
  "진흥왕": "삼국 시대",
  "비유왕": "삼국 시대",
  "눌지왕": "삼국 시대",
  "근초고왕": "삼국 시대",
  "광개토대왕": "삼국 시대",
  "백제 금동대향로": "삼국 시대",
  "발해": "남북국 시대",
  "대무예": "남북국 시대",
  "정효 공주": "남북국 시대",
  "정효공주": "남북국 시대",
  "해동성국": "남북국 시대",
  "통일 신라": "남북국 시대",
  "통일신라": "남북국 시대",
  "신문왕": "남북국 시대",
  "장보고": "남북국 시대",
  "원효": "남북국 시대",
  "의상": "남북국 시대",
  "후백제": "남북국 시대",
  "견훤": "남북국 시대",
  "고려": "고려",
  "광종": "고려",
  "공민왕": "고려",
  "무신 정권": "고려",
  "무신정권": "고려",
  "몽골": "고려",
  "원 간섭기": "고려",
  "팔만대장경": "고려",
  "직지심체요절": "고려",
  "향교": "고려",
  "조선": "조선",
  "세종": "조선",
  "장영실": "조선",
  "자격루": "조선",
  "훈민정음": "조선",
  "경국대전": "조선",
  "사화": "조선",
  "조의제문": "조선",
  "임진왜란": "조선",
  "정조": "조선",
  "균역법": "조선",
  "대동법": "조선",
  "비변사": "조선",
  "홍경래": "조선",
  "세도 정치": "조선",
  "세도정치": "조선",
  "원납전": "조선",
  "경복궁 중건": "조선",
  "영건일감": "조선",
  "성호사설": "조선",
  "곤여만국전도": "조선",
  "박제가": "조선",
  "박지원": "조선",
  "김홍도": "조선",
  "신윤복": "조선",
  "몽유도원도": "조선",
  "개항": "개항기",
  "강화도 조약": "개항기",
  "강화도조약": "개항기",
  "운요호": "개항기",
  "조선책략": "개항기",
  "황준헌": "개항기",
  "황쭌쉔": "개항기",
  "임오군란": "개항기",
  "갑신정변": "개항기",
  "동학 농민 운동": "개항기",
  "동학농민운동": "개항기",
  "갑오개혁": "개항기",
  "삼국 간섭": "개항기",
  "독립협회": "개항기",
  "대한 제국": "개항기",
  "대한제국": "개항기",
  "환구단": "개항기",
  "한성 전기 회사": "개항기",
  "한성전기회사": "개항기",
  "전차": "개항기",
  "을사늑약": "개항기",
  "헤이그 특사": "개항기",
  "일제": "일제 강점기",
  "3·1 운동": "일제 강점기",
  "3·1운동": "일제 강점기",
  "6·10 만세 운동": "일제 강점기",
  "물산 장려": "일제 강점기",
  "물산장려": "일제 강점기",
  "소년 운동": "일제 강점기",
  "어린이날": "일제 강점기",
  "박은식": "일제 강점기",
  "한국독립운동지혈사": "일제 강점기",
  "미쓰야": "일제 강점기",
  "미쓰야 협정": "일제 강점기",
  "대한민국 임시 정부": "일제 강점기",
  "대한민국 임시정부": "일제 강점기",
  "신간회": "일제 강점기",
  "의열단": "일제 강점기",
  "한국광복군": "일제 강점기",
  "광복": "현대",
  "모스크바 3상 회의": "현대",
  "좌우 합작": "현대",
  "대한민국 정부": "현대",
  "6·25": "현대",
  "4·19": "현대",
  "5·16": "현대",
  "5.16": "현대",
  "5·18": "현대",
  "6월 민주 항쟁": "현대",
  "교복과 두발": "현대",
  "야간 통행 금지": "현대",
  "보도 지침": "현대",
  "전두환": "현대",
  "남북": "현대",
  "통일": "현대",
  "광주 대단지 사건": "현대",
  "행정 중심 복합 도시": "현대",
};

const FIELDNAMES = [
  "ml_sequence_index",
  "split",
  "round_no",
  "question_no",
  "problem_id",
  "data_source",
  "input_text",
  "keywords",
  "era",
  "topic",
  "question_type",
  "question_subtype",
  "core_concept",
];

function readJson(filePath, fallback) {
  if (!fs.existsSync(filePath)) return fallback;
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

function normalizeText(value) {
  return String(value || "").trim();
}

function noSpace(value) {
  return normalizeText(value).replace(/\s+/g, "");
}

function normalizeLabel(value, allowed, fallback) {
  const text = ERA_ALIAS[normalizeText(value)] || normalizeText(value);
  if (allowed.includes(text)) return text;
  const compact = noSpace(text);
  const exact = allowed.find((item) => noSpace(item) === compact);
  if (exact) return exact;
  const partial = allowed.find((item) => text.includes(item) || item.includes(text));
  return partial || fallback;
}

function buildPersonIndex() {
  const data = readJson(PERSON_JSON, {});
  const index = new Map();
  const entities = data._entity_index || {};
  for (const entry of Object.values(entities)) {
    const name = normalizeText(entry.name);
    const appearances = entry.appearances || [];
    if (!name || entry.multi_era || appearances.length === 0) continue;
    const era = ERA_ALIAS[normalizeText(appearances[0].era)] || normalizeText(appearances[0].era);
    if (!ERA_VALUES.includes(era)) continue;
    index.set(name, era);
    index.set(noSpace(name), era);
  }
  return index;
}

function flattenKeywordGroups(reference, eraOverrides, personIndex) {
  const keywords = [];
  for (const keyword of Object.keys(MANUAL_ERA_OVERRIDES)) {
    keywords.push({ keyword: normalizeText(keyword), group: MANUAL_ERA_OVERRIDES[keyword] });
  }
  for (const [era, values] of Object.entries(reference.era_keywords || {})) {
    for (const keyword of values || []) keywords.push({ keyword: normalizeText(keyword), group: ERA_ALIAS[era] || era });
  }
  for (const [topic, values] of Object.entries(reference.topic_keywords || {})) {
    for (const keyword of values || []) keywords.push({ keyword: normalizeText(keyword), group: topic });
  }
  for (const [era, values] of Object.entries(eraOverrides || {})) {
    for (const keyword of values || []) keywords.push({ keyword: normalizeText(keyword), group: era });
  }
  for (const name of personIndex.keys()) {
    if (/\s/.test(name)) keywords.push({ keyword: normalizeText(name), group: "인물" });
  }

  const deduped = new Map();
  for (const item of keywords) {
    if (!item.keyword || item.keyword.length < 2) continue;
    deduped.set(noSpace(item.keyword), item.keyword);
  }
  return Array.from(deduped.values()).sort((a, b) => b.length - a.length);
}

function extractKeywords(inputText, coreConcept, keywordList) {
  const source = normalizeText(inputText);
  const compactSource = noSpace(source);
  const found = [];
  const seen = new Set();

  const addKeyword = (keyword) => {
    const compact = noSpace(keyword);
    if (!compact || seen.has(compact)) return;
    seen.add(compact);
    found.push(normalizeText(keyword));
  };

  if (coreConcept && source.includes(coreConcept)) addKeyword(coreConcept);

  for (const keyword of keywordList) {
    const compact = noSpace(keyword);
    if (compact && compactSource.includes(compact)) addKeyword(keyword);
    if (found.length >= 12) break;
  }

  if (found.length === 0 && coreConcept && coreConcept !== "미분류") addKeyword(coreConcept);
  return found.join(" ");
}

function inferEra(coreConcept, inputText, labelText, personIndex, reference, eraOverrides) {
  const coreCompact = noSpace(coreConcept);
  const textCompact = noSpace(`${coreConcept}\n${labelText}\n${inputText}`);

  for (const [keyword, era] of Object.entries(MANUAL_ERA_OVERRIDES)) {
    const compact = noSpace(keyword);
    if (compact && textCompact.includes(compact)) return normalizeLabel(era, ERA_VALUES, "미분류");
  }

  for (const [era, values] of Object.entries(eraOverrides || {})) {
    const normalizedEra = ERA_ALIAS[era] || era;
    for (const keyword of values || []) {
      const compact = noSpace(keyword);
      if (compact && textCompact.includes(compact)) return normalizeLabel(normalizedEra, ERA_VALUES, "미분류");
    }
  }

  if (personIndex.has(coreCompact)) return personIndex.get(coreCompact);
  for (const [name, era] of personIndex.entries()) {
    const compact = noSpace(name);
    if (compact && (coreCompact.includes(compact) || textCompact.includes(compact))) return era;
  }

  const candidates = [];
  for (const [era, values] of Object.entries(reference.era_keywords || {})) {
    const normalizedEra = normalizeLabel(ERA_ALIAS[era] || era, ERA_VALUES, "");
    if (!normalizedEra) continue;
    for (const keyword of values || []) {
      const compact = noSpace(keyword);
      if (compact && textCompact.includes(compact)) candidates.push({ length: compact.length, era: normalizedEra });
    }
  }
  candidates.sort((a, b) => b.length - a.length);
  return candidates[0]?.era || "미분류";
}

function fallbackEraByQuestionNo(questionNo) {
  const q = Number(questionNo);
  if (q <= 1) return "선사 시대";
  if (q <= 2) return "초기 국가";
  if (q <= 5) return "삼국 시대";
  if (q <= 9) return "남북국 시대";
  if (q <= 16) return "고려";
  if (q <= 29) return "조선";
  if (q <= 36) return "개항기";
  if (q <= 45) return "일제 강점기";
  return "현대";
}

function inferTopic(item, reference) {
  const topicType = normalizeText(item.topic_type);
  if (TOPIC_TYPE_TO_TOPIC[topicType]) return TOPIC_TYPE_TO_TOPIC[topicType];

  const source = `${normalizeText(item.topic)}\n${normalizeText(item.input_text)}`;
  const candidates = [];
  for (const [topic, values] of Object.entries(reference.topic_keywords || {})) {
    if (!TOPIC_VALUES.includes(topic)) continue;
    for (const keyword of values || []) {
      const text = normalizeText(keyword);
      if (text && source.includes(text)) candidates.push({ length: text.length, topic });
    }
  }
  candidates.sort((a, b) => b.length - a.length);
  return candidates[0]?.topic || "정치";
}

function inferQuestionType(item) {
  const majorType = normalizeText(item.major_type);
  if (QUESTION_TYPES.includes(majorType)) return majorType;
  const taskType = normalizeText(item.question_task);
  return QUESTION_TASK_TO_TYPE[taskType] || normalizeLabel(majorType, QUESTION_TYPES, "역사 지식의 이해");
}

function inferQuestionSubtype(item) {
  return normalizeLabel(normalizeText(item.minor_type), QUESTION_SUBTYPES, "기본 사실·개념 확인");
}

function extractCoreConcept(item, inputText, keywordList) {
  const topic = normalizeText(item.topic);
  if (topic && topic.length <= 40) return topic;
  const compactSource = noSpace(inputText);
  for (const keyword of keywordList) {
    if (compactSource.includes(noSpace(keyword))) return keyword;
  }
  return "미분류";
}

function toCsvValue(value) {
  const text = String(value ?? "");
  return `"${text.replace(/"/g, '""')}"`;
}

function writeCsv(filePath, rows) {
  const lines = [FIELDNAMES.join(",")];
  for (const row of rows) {
    lines.push(FIELDNAMES.map((field) => toCsvValue(row[field])).join(","));
  }
  fs.writeFileSync(filePath, `${lines.join("\n")}\n`, "utf8");
}

function countBy(rows, field) {
  const counts = {};
  for (const row of rows) {
    const key = normalizeText(row[field]) || "(blank)";
    counts[key] = (counts[key] || 0) + 1;
  }
  return Object.fromEntries(Object.entries(counts).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], "ko")));
}

function main() {
  const sourceRows = readJson(INPUT_JSON, []);
  const reference = readJson(REFERENCE_JSON, {});
  const eraOverrides = readJson(ERA_OVERRIDES_JSON, {});
  const personIndex = buildPersonIndex();
  const keywordList = flattenKeywordGroups(reference, eraOverrides, personIndex);

  const rows = sourceRows.map((item) => {
    const inputText = [normalizeText(item.material), normalizeText(item.question)].filter(Boolean).join("\n");
    const coreConcept = extractCoreConcept(item, inputText, keywordList);
    const labelText = [
      normalizeText(item.topic),
      normalizeText(item.answer_choice),
      ...(item.choices || []).filter((choice) => choice && choice.is_answer).map((choice) => normalizeText(choice.content)),
    ].filter(Boolean).join("\n");
    const roundNo = Number(item.round_no);
    const inferredEra = inferEra(coreConcept, inputText, labelText, personIndex, reference, eraOverrides);
    const finalEra = inferredEra === "미분류" ? fallbackEraByQuestionNo(item.question_no) : inferredEra;
    const keywords = extractKeywords(inputText, coreConcept, keywordList) || finalEra;
    return {
      ml_sequence_index: Number(item.ml_sequence_index),
      split: roundNo <= 70 ? "train" : "test",
      round_no: roundNo,
      question_no: Number(item.question_no),
      problem_id: normalizeText(item.problem_id),
      data_source: normalizeText(item.data_source),
      input_text: inputText,
      keywords,
      era: finalEra,
      topic: inferTopic(item, reference),
      question_type: inferQuestionType(item),
      question_subtype: inferQuestionSubtype(item),
      core_concept: coreConcept === "미분류" ? keywords : coreConcept,
    };
  });

  fs.mkdirSync(OUT_DIR, { recursive: true });
  fs.writeFileSync(OUTPUT_JSON, `${JSON.stringify(rows, null, 2)}\n`, "utf8");
  writeCsv(OUTPUT_CSV, rows);

  const report = {
    input: path.relative(ROOT_DIR, INPUT_JSON).replace(/\\/g, "/"),
    outputs: {
      json: path.relative(ROOT_DIR, OUTPUT_JSON).replace(/\\/g, "/"),
      csv: path.relative(ROOT_DIR, OUTPUT_CSV).replace(/\\/g, "/"),
    },
    total_rows: rows.length,
    split_counts: countBy(rows, "split"),
    era_counts: countBy(rows, "era"),
    topic_counts: countBy(rows, "topic"),
    question_type_counts: countBy(rows, "question_type"),
    question_subtype_counts: countBy(rows, "question_subtype"),
    missing_label_rows: rows
      .filter((row) => row.era === "미분류" || !row.keywords || row.core_concept === "미분류")
      .map((row) => ({
        round_no: row.round_no,
        question_no: row.question_no,
        era: row.era,
        keywords: row.keywords,
        core_concept: row.core_concept,
      })),
  };
  fs.writeFileSync(REPORT_JSON, `${JSON.stringify(report, null, 2)}\n`, "utf8");

  console.log(JSON.stringify({
    rows: rows.length,
    split_counts: report.split_counts,
    output_json: report.outputs.json,
    output_csv: report.outputs.csv,
    report_json: path.relative(ROOT_DIR, REPORT_JSON).replace(/\\/g, "/"),
    missing_label_rows: report.missing_label_rows.length,
  }, null, 2));
}

main();
