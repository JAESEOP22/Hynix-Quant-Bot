1. 주요 아키텍처 및 데이터 조작화 기법

- 정교한 NLP 가치평가 및 엄격한 배제 규칙 (Strict Exclusion)
단순 긍정/부정 감성 분석을 넘어 뉴스의 펀더멘털 임팩트를 가치평가 4요소(매출, 마진, 재투자, 리스크) 관점에서 심층 평가합니다. 특히 하청 장비사나 테마주, 주관적인 목표주가 유지 뉴스 등 SK하이닉스 본체의 직접적 재무 영향이 없는 노이즈 정보는 엄격히 스크리닝하여 점수를 0점 처리(배제)합니다.

- 매크로 및 기술적 필터 결합
미국 필라델피아 반도체 지수(SOXX)의 전일 종가 추세를 통해 글로벌 업황 방향성을 동기화합니다. 동시에 한국 시장 개장 전 단기 14일 RSI를 연산하여 과매수 구간(RSI 70 이상)에서의 뇌동매매 및 추격 매수 위험을 하드웨어 레벨에서 필터링합니다.

- 동적 리스크 통제 및 미시구조적 체결 전략

- 09:01 지연 진입: 09:00 동시호가 체결 시 발생하는 인위적인 호가 스프레드 왜곡과 슬리피지(Slippage) 비용을 방어하기 위해 의도적으로 1분의 가격 발견 시간을 부여합니다.

- 3중 청산 매커니즘: 포지션 진입 후 장중 실시간으로 +3.0% 도달 시 즉시 익절(Take Profit), -2.0% 도달 시 즉시 손절(Stop Loss)을 수행합니다. 두 청산 트리거가 발동하지 않은 경우 09:15에 시간 만료(Timeout)로 일괄 일시적 시장가 청산을 수행해 오버나이트 리스크를 완벽하게 배제합니다.

- 프로덕션 환경의 복원력 (Resilience) 설계

- 타임 시프팅(08:45): 08:30 정각에 전 세계 시스템이 동시에 API를 호출할 때 발생하는 구글 서버의 일시적 과부하(503 에러)를 회피하기 위해 데이터 분석 시점을 08:45로 최적화 이동했습니다.

- 지수 백오프(Exponential Backoff): 트래픽 제한 감지 시 대기 시간을 기하급수적(5초, 10초, 20초, 40초, 80초)으로 늘려가며 최대 5회 끈질기게 다시 요청합니다.

- 증권사 서버 점검 대응: 국내 증권사의 자정 정검 시간대를 교묘하게 우회하여, 장 시작 전 서버가 가장 안정화되는 아침 08:00에 API 토큰을 발급받아 비정상적인 세션 끊김 현상을 방지합니다.



2. 설치 및 개발 환경 세팅 (Ubuntu 기준)

- 이 프로젝트는 Ubuntu 20.04/22.04 LTS 환경에서의 백그라운드 상시 구동을 상정하여 패키징되었습니다.

- 리포지토리 클론 및 폴더 이동

git clone [https://github.com/JAESEOP22/Hynix-Quant-Bot.git](https://github.com/JAESEOP22/Hynix-Quant-Bot.git)
cd Hynix-Quant-Bot


- 가상환경(venv) 구축 및 의존성 패키지 설치

python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install fastapi uvicorn requests yfinance pandas python-dotenv google-genai python-dateutil


- 환경 변수 세팅 (.env)
프로젝트 루트 디렉토리에 .env 파일을 생성하고 아래 보안 정보를 기재합니다. (.gitignore 처리로 깃허브에는 제외됨)

GEMINI_API_KEY=your_gemini_api_key
Client_ID=your_naver_news_client_id
Client_Secret=your_naver_news_client_secret
KIS_API_KEY=your_korea_investment_appkey
KIS_SECRET_KEY=your_korea_investment_appsecret
KIS_ACCOUNT_NO=your_korean_investment_account_number_with_dash



3. Ubuntu 상시 가동 설정 (Background Execution)

- 알고리즘의 상시 자동 매매를 실현하기 위해 Ubuntu 백그라운드 프로세스 매니저를 사용하는 것이 권장됩니다.

- 방법 A: Tmux 터미널 세션 유지 (권장)

# 새 가상 세션 열기
tmux new -s trading_session

# 가상환경 활성화 및 서비스 구동
source venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8000

# 세션에서 빠져나오기 (서버 계속 가동됨)
Ctrl + B 누른 뒤 D 키 입력

# 다시 세션으로 진입하여 로그 확인하기
tmux attach -t trading_session


- 방법 B: Systemd 서비스 등록 (정식 데몬 등록)

sudo nano /etc/systemd/system/trading.service


- 파일 안에 아래 구성 정보를 입력하여 시스템 서비스로 등록합니다.

[Unit]
Description=SK Hynix Quant Trading Bot FastAPI Service
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/Hynix-Quant-Bot
ExecStart=/home/ubuntu/Hynix-Quant-Bot/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target


- 서비스 활성화 명령:

sudo systemctl daemon-reload
sudo systemctl start trading
sudo systemctl enable trading
# 작동 로그 확인
sudo journalctl -u trading -f



4. 거래 로그 분석 구조 (valuation_trading_log.csv)

- 시스템 구동 결과는 매일 로컬 CSV 데이터베이스에 누적 기재됩니다. 단순히 거래 성공(TRADE) 기록뿐만 아니라, 시스템이 왜 진입을 웅크렸는지(SKIP), 네트워크 장애나 인프라 상의 누락(FAIL)은 어떻게 발생했는지 자체 평가 모듈의 퀀트 피드백이 정성 데이터로 축적되어, 지속적인 전략 디버깅의 유용한 자료로 축적됩니다.
