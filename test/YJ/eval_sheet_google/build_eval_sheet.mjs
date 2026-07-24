import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const here = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1"));
const data = JSON.parse(await fs.readFile(path.join(here, "questions.json"), "utf8"));
const outputDir = path.resolve(here, "outputs");
const outputPath = path.join(outputDir, "hanneung_50_eval_sheet_20260703.xlsx");

const wb = Workbook.create();
const summary = wb.worksheets.add("요약");
const main = wb.worksheets.add("평가_메인");
const source = wb.worksheets.add("문항_원문");
const rubric = wb.worksheets.add("평가기준_요약");

const gateHeaders = ["G1", "G2", "G3", "G4", "G5", "G6"];
const mainHeaders = [
  "번호", "배점", "정답",
  ...gateHeaders.map((g) => `Codex ${g}`), "Codex Gate", "Codex 조치", "Codex 난이도(4)", "Codex 선택지품질(6)", "Codex 문제점수(10)", "Codex 사유",
  ...gateHeaders.map((g) => `Human ${g}`), "Human Gate", "Human 조치", "Human 난이도(4)", "Human 선택지품질(6)", "Human 문제점수(10)", "Human 사유",
  "Gate 일치", "조치 일치", "점수차", "최종 일치", "비고",
];

main.getRange("A1:AF1").values = [mainHeaders];
main.getRange("A2:C51").values = data.questions.map((q) => [q.number, q.target_score, q.answer_label]);

for (let row = 2; row <= 51; row++) {
  main.getRange(`J${row}`).formulas = [[`=IF(COUNTA(D${row}:I${row})=0,"",IF(COUNTIF(D${row}:I${row},"FAIL")>0,"FAIL",IF(COUNTIF(D${row}:I${row},"uncertain")>0,"uncertain","PASS")))`]];
  main.getRange(`N${row}`).formulas = [[`=IF(J${row}="","",IF(J${row}="PASS",SUM(L${row}:M${row}),0))`]];
  main.getRange(`V${row}`).formulas = [[`=IF(COUNTA(P${row}:U${row})=0,"",IF(COUNTIF(P${row}:U${row},"FAIL")>0,"FAIL",IF(COUNTIF(P${row}:U${row},"uncertain")>0,"uncertain","PASS")))`]];
  main.getRange(`Z${row}`).formulas = [[`=IF(V${row}="","",IF(V${row}="PASS",SUM(X${row}:Y${row}),0))`]];
  main.getRange(`AB${row}`).formulas = [[`=IF(OR(J${row}="",V${row}=""),"",IF(J${row}=V${row},"일치","불일치"))`]];
  main.getRange(`AC${row}`).formulas = [[`=IF(OR(K${row}="",W${row}=""),"",IF(K${row}=W${row},"일치","불일치"))`]];
  main.getRange(`AD${row}`).formulas = [[`=IF(OR(N${row}="",Z${row}=""),"",ABS(N${row}-Z${row}))`]];
  main.getRange(`AE${row}`).formulas = [[`=IF(OR(AB${row}="",AD${row}=""),"",IF(AND(AB${row}="일치",AD${row}<=1),"일치","검토"))`]];
}

const sourceHeaders = ["번호", "배점", "정답", "발문·자료", "①", "②", "③", "④", "⑤", "선지수"];
source.getRange("A1:J1").values = [sourceHeaders];
source.getRange(`A2:J${data.questions.length + 1}`).values = data.questions.map((q) => [
  q.number, q.target_score, q.answer_label, q.stem_material,
  q.choice_1, q.choice_2, q.choice_3, q.choice_4, q.choice_5, q.choice_count,
]);

summary.getRange("A1:D1").merge();
summary.getRange("A1").values = [["한능검 50문항 평가 일치율 대시보드"]];
summary.getRange("A3:B15").values = [
  ["항목", "값"],
  ["전체 문항 수", null],
  ["Codex Gate PASS", null],
  ["Human Gate PASS", null],
  ["Gate 양쪽 입력 완료", null],
  ["Gate 일치율", null],
  ["최종 일치율", null],
  ["Codex 평균 문제점수", null],
  ["Human 평균 문제점수", null],
  ["평균 점수차", null],
  ["Codex 통과문항 평균", null],
  ["Human 통과문항 평균", null],
  ["검토 필요 문항 수", null],
];
summary.getRange("B4:B15").formulas = [
  [`=COUNTA('평가_메인'!A2:A51)`],
  [`=COUNTIF('평가_메인'!J2:J51,"PASS")`],
  [`=COUNTIF('평가_메인'!V2:V51,"PASS")`],
  [`=SUMPRODUCT(--('평가_메인'!J2:J51<>""),--('평가_메인'!V2:V51<>""))`],
  [`=IFERROR(COUNTIF('평가_메인'!AB2:AB51,"일치")/SUMPRODUCT(--('평가_메인'!AB2:AB51<>"")),"")`],
  [`=IFERROR(COUNTIF('평가_메인'!AE2:AE51,"일치")/SUMPRODUCT(--('평가_메인'!AE2:AE51<>"")),"")`],
  [`=IFERROR(AVERAGEIF('평가_메인'!N2:N51,"<>"),"")`],
  [`=IFERROR(AVERAGEIF('평가_메인'!Z2:Z51,"<>"),"")`],
  [`=IFERROR(AVERAGEIF('평가_메인'!AD2:AD51,"<>"),"")`],
  [`=IFERROR(AVERAGEIF('평가_메인'!J2:J51,"PASS",'평가_메인'!N2:N51),"")`],
  [`=IFERROR(AVERAGEIF('평가_메인'!V2:V51,"PASS",'평가_메인'!Z2:Z51),"")`],
  [`=COUNTIF('평가_메인'!AE2:AE51,"검토")`],
];
summary.getRange("D3:D10").values = [
  ["사용법"],
  ["1. 평가_메인에서 Codex/Human Gate와 점수를 입력한다."],
  ["2. Gate가 하나라도 FAIL이면 점수는 0으로 계산된다."],
  ["3. Gate uncertain은 PASS가 아니므로 후속 검증 대상으로 둔다."],
  ["4. 최종 일치는 Gate가 같고 문제점수 차이가 1점 이하일 때 일치로 계산한다."],
  ["5. 문제 원문은 문항_원문 탭에서 확인한다."],
  ["6. 현행 기준은 평가기준_요약 탭에 압축했다."],
  ["7. 해설 평가는 이 PDF에 해설이 없어 제외했다."],
];

rubric.getRange("A1:D1").merge();
rubric.getRange("A1").values = [["한능검 SLLM 문항 평가지표 v1.8.3 요약"]];
rubric.getRange("A3:C9").values = [
  ["Gate", "항목", "FAIL 기준 요약"],
  ["G1", "입력·형식 성립", "발문·선택지·정답·배점 누락, 선택지 5개 아님, 정답 1개 아님"],
  ["G2", "발문·선지 판독 가능성", "문장 파손·누락·심한 비문, 발문 요구 형식과 선택지 형식 불일치"],
  ["G3", "정답 성립성·유일성", "발문 조건을 만족하는 선택지가 0개 또는 2개 이상, 표시 정답 오류"],
  ["G4", "발문·자료 사실성", "발문·자료 핵심 단서가 역사 사실과 명백히 충돌하거나 내부 모순"],
  ["G5", "오답 역사 사실성", "가짜 용어·가짜 사건·허위 결합. 다른 실제 역사 사실이면 PASS"],
  ["G6", "정답 노출·복사·대상명 노출·외형 편향", "정답명 직접 노출, 정답 선지 진술 복사, 대상명만으로 정답 확정, 정답만 외형적으로 튐"],
];
rubric.getRange("A11:C15").values = [
  ["점수 항목", "범위", "입력 기준"],
  ["목표 난이도 적합성", "0~4점", "배점에 맞는 단서 수, 단서 간접성, 풀이 단계, 지식 깊이"],
  ["선택지 품질", "0~6점", "선택지 비교 단위, 중복 없음, 유효 매력 오답 수"],
  ["문제점수", "0~10점", "Gate PASS일 때 난이도+선택지품질, Gate FAIL이면 0점"],
  ["해설 품질", "제외", "첨부 PDF에 해설이 없어 이번 평가 시트에서는 제외"],
];

for (const sheet of [summary, main, source, rubric]) {
  sheet.showGridLines = false;
}

const headerFill = "#1F4E78";
const headerFont = { bold: true, color: "#FFFFFF" };
for (const [sheet, range] of [[main, "A1:AF1"], [source, "A1:J1"], [rubric, "A3:C3"], [rubric, "A11:C11"], [summary, "A3:B3"]]) {
  sheet.getRange(range).format = { fill: headerFill, font: headerFont, wrapText: true };
}
summary.getRange("A1:D1").format = { fill: "#17365D", font: { bold: true, color: "#FFFFFF", size: 16 }, horizontalAlignment: "center" };
rubric.getRange("A1:D1").format = { fill: "#17365D", font: { bold: true, color: "#FFFFFF", size: 14 }, horizontalAlignment: "center" };

main.getRange("A1:AF51").format.borders = { preset: "all", style: "thin", color: "#D9E2F3" };
source.getRange("A1:J51").format.borders = { preset: "all", style: "thin", color: "#D9E2F3" };
rubric.getRange("A3:C15").format.borders = { preset: "all", style: "thin", color: "#D9E2F3" };
summary.getRange("A3:B15").format.borders = { preset: "all", style: "thin", color: "#D9E2F3" };

main.getRange("J2:J51").format = { fill: "#E2F0D9", font: { bold: true } };
main.getRange("V2:V51").format = { fill: "#E2F0D9", font: { bold: true } };
main.getRange("AB2:AE51").format = { fill: "#FCE4D6", font: { bold: true } };
main.getRange("N2:N51").format = { fill: "#EAF2F8", font: { bold: true } };
main.getRange("Z2:Z51").format = { fill: "#EAF2F8", font: { bold: true } };

main.getRange("A:C").format.columnWidth = 8;
main.getRange("D:I").format.columnWidth = 13;
main.getRange("J:K").format.columnWidth = 13;
main.getRange("L:N").format.columnWidth = 13;
main.getRange("O:O").format.columnWidth = 36;
main.getRange("P:U").format.columnWidth = 13;
main.getRange("V:W").format.columnWidth = 13;
main.getRange("X:Z").format.columnWidth = 13;
main.getRange("AA:AA").format.columnWidth = 36;
main.getRange("AB:AF").format.columnWidth = 13;
main.getRange("A1:AF51").format.wrapText = true;

source.getRange("A:C").format.columnWidth = 8;
source.getRange("D:D").format.columnWidth = 70;
source.getRange("E:I").format.columnWidth = 32;
source.getRange("J:J").format.columnWidth = 8;
source.getRange("A1:J51").format.wrapText = true;

rubric.getRange("A:A").format.columnWidth = 14;
rubric.getRange("B:B").format.columnWidth = 28;
rubric.getRange("C:C").format.columnWidth = 90;
rubric.getRange("A1:C15").format.wrapText = true;
summary.getRange("A:A").format.columnWidth = 28;
summary.getRange("B:B").format.columnWidth = 18;
summary.getRange("D:D").format.columnWidth = 72;
summary.getRange("A1:D15").format.wrapText = true;
summary.getRange("B9:B10").format.numberFormat = "0.0";
summary.getRange("B11:B12").format.numberFormat = "0.0";
summary.getRange("B8:B9").format.numberFormat = "0.0";
summary.getRange("B8:B9").format.numberFormat = "0.0%";

const gateValidation = { rule: { type: "list", values: ["PASS", "FAIL", "uncertain", "N/A"] } };
const actionValidation = { rule: { type: "list", values: ["accept", "repair", "regenerate", "needs_verification", "discard"] } };
for (const r of ["D2:I51", "P2:U51"]) main.getRange(r).dataValidation = gateValidation;
for (const r of ["K2:K51", "W2:W51"]) main.getRange(r).dataValidation = actionValidation;
for (const r of ["L2:L51", "X2:X51"]) main.getRange(r).dataValidation = { rule: { type: "whole", operator: "between", formula1: 0, formula2: 4 } };
for (const r of ["M2:M51", "Y2:Y51"]) main.getRange(r).dataValidation = { rule: { type: "whole", operator: "between", formula1: 0, formula2: 6 } };

main.freezePanes.freezeRows(1);
main.freezePanes.freezeColumns(3);
source.freezePanes.freezeRows(1);
source.freezePanes.freezeColumns(3);

const inspect = await wb.inspect({ kind: "table", sheetId: "평가_메인", range: "A1:AF6", tableMaxRows: 6, tableMaxCols: 32, maxChars: 4000 });
console.log(inspect.ndjson);
const errors = await wb.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A", options: { useRegex: true, maxResults: 100 }, summary: "formula error scan" });
console.log(errors.ndjson);

await fs.mkdir(outputDir, { recursive: true });
const preview = await wb.render({ sheetName: "요약", autoCrop: "all", scale: 1, format: "png" });
await fs.writeFile(path.join(outputDir, "preview_summary.png"), new Uint8Array(await preview.arrayBuffer()));
const xlsx = await SpreadsheetFile.exportXlsx(wb);
await xlsx.save(outputPath);
console.log(outputPath);
process.exit(0);
