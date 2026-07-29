\# HiMate ECS on EC2 배포



\## 구성



```text

Route53

&#x20; → Public EC2의 ECS Nginx Task (80/443)

&#x20; → 같은 Web Task의 Gunicorn/Django (8000)

&#x20; → Private Aurora PostgreSQL (5432)



Django

&#x20; → 별도 Neo4j ECS Task (7687)

&#x20; → EC2 EBS에 데이터 저장



CodePipeline → CodeBuild → ECR → ECS Service

```



초기 저비용 구성에서는 ALB와 NAT Gateway를 사용하지 않는다.



\## 주요 설정



\- Public EC2 subnet 1개

\- Private RDS subnet 2개

\- Aurora Serverless v2: 최소 `0 ACU`, 최대 `1 ACU`

\- Aurora auto-pause: `300초`

\- `POSTGRES\_CONN\_MAX\_AGE=0`

\- `POSTGRES\_CONNECT\_TIMEOUT\_SECONDS=30`

\- 운영 환경변수 prefix: `/himate/prod`

\- 비밀번호와 API key는 Parameter Store `SecureString` 사용

\- ECS 상태 확인 경로: `/health/live/`

\- ECR tag immutability와 scan on push 활성화



\## 배포 파일



\- `service-task-definition.json.template`: Web·Nginx Task

\- `migration-task-definition.json.template`: Django migration Task

\- `neo4j-task-definition.json.template`: Neo4j Task

\- `ssm-parameters.example`: Parameter Store 항목

\- `../nginx/`: Nginx와 Certbot 설정

\- `../../buildspec.yml`: CodeBuild 빌드·검사·ECR push



\## 최초 배포 순서



1\. VPC, Aurora, Parameter Store와 IAM 구성

2\. ECS용 EC2와 EBS 구성

3\. CodeBuild로 이미지를 ECR에 push

4\. migration Task 실행

5\. Neo4j와 Web ECS Service 실행

6\. HTTPS와 `/health/live/` 확인

7\. CodePipeline 배포 단계 연결

