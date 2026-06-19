# CSS 정리 순서

## 현재 기준

현재 CSS는 다음 3단 구조로 정리한다.

1. `app/static/himate.css`
   - 공통 색상 토큰
   - 공통 네비게이션 바
   - 공통 로고
   - 공통 버튼 스타일

2. `app/static/layout.css`
   - 공통 페이지 배경
   - 공통 카드, 패널, 폼 스타일
   - 여러 페이지에서 반복되는 레이아웃 보정

3. 페이지별 CSS
   - 각 화면에만 필요한 배치
   - 챗봇 3단 레이아웃
   - 문제풀이 화면 구성
   - 결과 페이지 구성
   - 마이페이지 대시보드, 달력
   - 오답노트 3단 구조

## 바로 삭제하면 안 되는 파일

아래 파일들은 아직 페이지 고유 레이아웃을 담당하므로 바로 삭제하지 않는다.

- `app/static/chatbot/css/chat.css`
- `app/static/diagnosis/css/intro.css`
- `app/static/diagnosis/css/exam.css`
- `app/static/diagnosis/css/result.css`
- `app/static/pages/css/index.css`
- `app/static/question/css/create.css`
- `app/static/question/css/exam.css`
- `app/static/question/css/result.css`
- `app/static/user/css/login.css`
- `app/static/user/css/register.css`
- `app/static/user/css/mypage.css`
- `app/static/user/css/wrong_note.css`

## 정리 순서

### 1. 공통 스타일 기준 확정

먼저 `himate.css`, `layout.css`를 기준으로 삼는다.

- 색상은 `--hm-*` 토큰 사용
- 네비게이션은 `.site-header`, `.chat-header` 규칙 사용
- 버튼은 `.primary-button`, `.ghost-button`, `.outline-button`, `.pill-button` 규칙 사용
- 공통 카드와 패널은 `layout.css`에서 관리

### 2. 페이지별 CSS에서 공통 스타일 제거

각 페이지별 CSS에서 아래 항목을 제거하거나 최소화한다.

- `:root` 색상 토큰 중 공통 토큰과 겹치는 값
- `body` 배경, 폰트, 기본 색상
- `.site-header`, `.chat-header`
- `.brand`
- `.main-nav`
- `.header-actions`
- `.ghost-button`, `.primary-button`, `.submit-button` 등 공통 버튼
- 공통 카드 border, radius, shadow

단, 화면 배치에 필요한 규칙은 남긴다.

### 3. 페이지별 레이아웃만 남기기

페이지별 CSS에는 아래처럼 해당 화면에만 필요한 것만 남긴다.

- `chat.css`: 채팅 3단 레이아웃, 메시지 버블, 답변 표, 이미지 갤러리
- `intro.css`: 진단평가 안내 화면의 단계/정보 테이블 배치
- `exam.css`: 문제 풀이 화면, 문항 목록, 타이머, 선택지
- `result.css`: 결과/해설 화면, 정답률 요약, 문제 번호 이동
- `index.css`: 메인 페이지 섹션, 스크롤 등장 효과
- `create.css`: 문제 생성 조건 행, 선택 버튼 배치
- `mypage.css`: 대시보드, 학습 계획, 달력
- `wrong_note.css`: 오답노트 문제/해설/목록 3단 구조
- `login.css`, `register.css`: 인증 폼 내부 배치

### 4. 중복 확인

정리 후 아래 명령으로 중복 후보를 찾는다.

```powershell
rg ":root|body|site-header|chat-header|main-nav|brand|ghost-button|primary-button|submit-button|box-shadow|border-radius" app\static -g "*.css"
```

중복이 남아 있어도 페이지 고유 레이아웃이면 유지한다.

### 5. 화면 검증

정리할 때마다 Django 렌더링을 확인한다.

```powershell
.\.venv\Scripts\python.exe app\manage.py check
```

주요 페이지도 확인한다.

```powershell
.\.venv\Scripts\python.exe app\manage.py shell -c "from django.test import Client; c=Client(); paths=['/','/diagnosis/','/diagnosis/exam/','/diagnosis/result/','/question/','/question/exam/','/question/result/','/chatbot/','/user/login/','/user/register/','/user/mypage/','/user/wrong-note/']; [print(p, c.get(p).status_code) for p in paths]"
```

### 6. 삭제 판단

아래 조건을 모두 만족할 때만 CSS 파일을 삭제한다.

- 해당 파일을 참조하는 템플릿이 없다.
- 해당 파일의 규칙이 `himate.css` 또는 `layout.css`로 완전히 이동됐다.
- 삭제 후 해당 페이지가 깨지지 않는다.
- `manage.py check`와 주요 페이지 200 확인을 통과한다.

## 권장 최종 구조

최종 목표는 아래 구조다.

```text
app/static/
  himate.css
  layout.css
  pages/css/index.css
  chatbot/css/chat.css
  diagnosis/css/intro.css
  diagnosis/css/exam.css
  diagnosis/css/result.css
  question/css/create.css
  question/css/exam.css
  question/css/result.css
  user/css/login.css
  user/css/register.css
  user/css/mypage.css
  user/css/wrong_note.css
```

페이지별 CSS는 없애는 것이 목표가 아니라, 공통 스타일을 제거하고 화면 고유 배치만 남기는 것이 목표다.
