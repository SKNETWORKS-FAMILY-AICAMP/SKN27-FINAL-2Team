# 수집 출처 robots.txt 확인

확인일: 2026-07-15

## 확인 범위

현재 `etl/crawling/`의 수집 스크립트와 이미지 출처 링크 CSV에서 실제 요청 대상인 도메인을 추출했다.

| 수집 대상 | 도메인 | robots.txt | 확인 결과 |
|---|---|---|---|
| 국사편찬위원회 한국사 콘텐츠 | `contents.history.go.kr` | 200 | 일반 크롤러 차단 |
| 한국고전종합DB 관계망 | `db.itkc.or.kr` | 200 | 전체 크롤러 차단 |
| 한국학중앙연구원 백과사전 API | `devin.aks.ac.kr:8080` | 404 | robots.txt 없음 |
| 한국민족문화대백과사전 웹 | `encykorea.aks.ac.kr` | 200 | 검색·작성자·해시태그 경로만 차단 |

## 원문과 해석

### contents.history.go.kr

URL: <https://contents.history.go.kr/robots.txt>

```txt
User-agent: Yeti
User-agent: Daum
User-agent: Googlebot
User-agent: bingbot
Allow: /

User-agent: *
Disallow: /
```

우리 수집기는 `Mozilla/5.0` 또는 별도 User-Agent로 요청하므로 `User-agent: *`에 해당한다. 따라서 현재 수집 경로는 robots 정책상 허용되지 않는다.

### db.itkc.or.kr

URL: <https://db.itkc.or.kr/robots.txt>

```txt
User-agent: *
Disallow: /
```

모든 크롤러의 전체 경로 수집을 차단한다. 현재 관계망 수집은 중단하고, 제공 API·다운로드 데이터·사전 허가 여부를 확인해야 한다.

### devin.aks.ac.kr:8080

URL: <https://devin.aks.ac.kr:8080/robots.txt>

HTTP 404를 반환했다. robots.txt가 없다는 사실만으로 API 사용이 허용되는 것은 아니다. 이 수집은 API 키를 사용하는 구조이므로 API 이용약관과 키 발급 조건을 기준으로 사용 가능 범위를 확인해야 한다.

### encykorea.aks.ac.kr

URL: <https://encykorea.aks.ac.kr/robots.txt>

```txt
User-agent: *
Disallow: /Article/Search
Disallow: /Media/Search
Disallow: /Article/WriterArticles
Disallow: /Article/Hashtag
```

개별 `Article` 및 `Media` 상세 페이지는 robots.txt상 차단 대상이 아니다. 다만 검색 결과·작성자별 목록·해시태그 목록 경로는 자동 수집하면 안 된다. 사이트맵은 `<https://encykorea.aks.ac.kr/sitemap.xml>`로 제공된다.

## 조치

- `contents.history.go.kr`, `db.itkc.or.kr`: robots 정책과 충돌하므로 추가 자동 수집을 진행하지 않는다.
- `devin.aks.ac.kr:8080`: API 약관 또는 제공자 허가를 확인한 뒤에만 수집을 계속한다.
- `encykorea.aks.ac.kr`: 상세 페이지 수집은 robots 정책상 가능하지만, 차단된 검색·목록 경로는 사용하지 않는다.
- 기존에 저장한 데이터의 보관·활용 가능 여부는 robots.txt와 별개로 각 사이트의 저작권·이용약관을 확인한다.
