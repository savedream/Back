# 프론트 개발용: 가짜 부품(모델·장치·음성·GPS)으로 백엔드를 띄운다. 실제 모델(torch)은 넣지 않는다
FROM python:3.12-slim

WORKDIR /app
COPY . .
# 매뉴얼 DB(data/)·정적 파일(static/) 경로가 소스 폴더 기준이라 그 자리에서 설치한다
RUN pip install --no-cache-dir -e .

# 컨테이너 밖에서 붙도록 모든 주소로 연다
ENV SALYEO_HOST=0.0.0.0
EXPOSE 8000

CMD ["python", "-m", "app.run"]
