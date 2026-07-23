import re
import unicodedata
from json import load

import pandas as pd


def clean_text(text: str) -> str:
    """
    문자열 하나를 정리하는 함수
    - 유니코드 NFC 정규화
    - \r, 탭을 각각 줄바꿈/공백으로 통일
    - 줄바꿈 주변 공백 제거, 연속 공백·연속 줄바꿈 축소
    - 앞뒤 공백 제거
    """
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def prep_json(json_path: str) -> pd.DataFrame:
    """
    기출문제 json 파일을 읽어 용어 추출용 DataFrame으로 전처리하는 함수

    - 모든 텍스트 필드에 clean_text 적용
    - 지문+질문(input_text)과 선지 전체를 합친 full_text 컬럼 생성
        (용어 추출은 full_text를 대상으로 함)
    - input_text 기준 완전 중복 문항 제거 (첫 번째만 유지)
    """
    raw = load(open(json_path, "r", encoding="utf-8"))
    df = pd.DataFrame(raw)

    text_columns = ["material", "question", "input_text", "answer_choice"]
    for column in text_columns:
        df[column] = df[column].map(clean_text)

    df["distractor_choices"] = df["distractor_choices"].map(
        lambda choices: [clean_text(choice) for choice in choices]
    )
    df["choices"] = df["choices"].map(
        lambda choices: [
            {"is_answer": choice["is_answer"], "content": clean_text(choice["content"])}
            for choice in choices
        ]
    )

    choice_text = df["choices"].map(
        lambda choices: "\n".join(choice["content"] for choice in choices)
    )
    df["full_text"] = df["input_text"] + "\n" + choice_text

    df = df.drop_duplicates(subset="input_text", keep="first")
    df = df.reset_index(drop=True)
    return df
