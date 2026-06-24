import os
import csv
import asyncio
import time
import requests
import re
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta, timezone
from dateutil import parser
from dotenv import load_dotenv
from fastapi import FastAPI
from google import genai

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
Client_ID = os.getenv("Client_ID")
Client_Secret = os.getenv("Client_Secret")
KIS_API_KEY = os.getenv("KIS_API_KEY")
KIS_SECRET_KEY = os.getenv("KIS_SECRET_KEY")
KIS_ACCOUNT_NO = os.getenv("KIS_ACCOUNT_NO")

client = genai.Client(api_key=GEMINI_API_KEY)
app = FastAPI()

KST = timezone(timedelta(hours=9))

TARGET_TICKER = "000660"
TARGET_NAME = "SK하이닉스"

TOTAL_BUDGET = 10000000
STOCK_ALLOCATION = 2000000

# 🛡️ 리스크 관리 파라미터 세팅
TAKE_PROFIT_PCT = 3.0  # 익절 라인 (+3%)
STOP_LOSS_PCT = -2.0   # 손절 라인 (-2%)

KIS_URL = "https://openapivts.koreainvestment.com:29443"
kis_token = ""
LOG_FILE = "valuation_trading_log.csv"

daily_analysis_done = False
position_held = False
buy_qty = 0

daily_trade_record = {
    "date": "", "status": "", "ai_insight": "", "rationale": "",
    "buy_time": "-", "buy_price": 0, "buy_qty": 0,
    "sell_time": "-", "sell_price": 0, "sell_qty": 0,
    "return_rate": 0.0, "current_cash": TOTAL_BUDGET
}

def fetch_kis_token():
    global kis_token
    url = f"{KIS_URL}/oauth2/tokenP"
    payload = {"grant_type": "client_credentials", "appkey": KIS_API_KEY, "appsecret": KIS_SECRET_KEY}
    res = requests.post(url, json=payload)
    if res.status_code == 200:
        kis_token = res.json().get("access_token")
        print(f"✅ [{datetime.now(KST).strftime('%H:%M:%S')}] 한투 API 토큰 갱신 완료")

def send_kis_order(side, qty):
    if not kis_token: return False
    url = f"{KIS_URL}/uapi/domestic-stock/v1/trading/order-cash"
    tr_id = "VTTC0802U" if side == "BUY" else "VTTC0801U"
    
    headers = {
        "Content-Type": "application/json", "Authorization": f"Bearer {kis_token}",
        "appkey": KIS_API_KEY, "appsecret": KIS_SECRET_KEY, "tr_id": tr_id
    }
    payload = {
        "CANO": KIS_ACCOUNT_NO, "ACNT_PRDT_CD": "01", "PDNO": TARGET_TICKER,
        "ORD_DVSN": "01", "ORD_QTY": str(qty), "ORD_UNPR": "0"
    }
    res = requests.post(url, json=payload, headers=headers)
    
    if res.status_code == 200 and res.json().get("rt_cd") == "0":
        return True
    else:
        print(f"❌ [한투 API] {side} 주문 실패 원인: {res.text}")
        return False

def get_current_price():
    if not kis_token: return 0
    url = f"{KIS_URL}/uapi/domestic-stock/v1/quotations/inquire-price"
    headers = {
        "Content-Type": "application/json", "Authorization": f"Bearer {kis_token}",
        "appkey": KIS_API_KEY, "appsecret": KIS_SECRET_KEY, "tr_id": "FHKST01010100"
    }
    params = {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": TARGET_TICKER}
    try:
        res = requests.get(url, headers=headers, params=params)
        if res.status_code == 200 and res.json().get('rt_cd') == "0":
            return int(res.json()['output']['stck_prpr'])
        else:
            print(f"❌ [현재가 조회 실패] {res.text}")
            return 0
    except Exception as e:
        print(f"❌ [현재가 조회 예외 발생] {e}")
        return 0


def check_macro_and_tech_filters():
    try:
        soxx = yf.download("^SOX", period="5d", progress=False)
        last_close = float(soxx['Close'].iloc[-1].item())
        prev_close = float(soxx['Close'].iloc[-2].item())
        soxx_positive = last_close > prev_close
        
        stock = yf.download("000660.KS", period="1mo", progress=False)
        delta = stock['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        last_rsi = float(rsi.iloc[-1].item())
        rsi_safe = last_rsi < 65
        
        return soxx_positive, last_rsi, rsi_safe
    except Exception:
        return False, 100, False

def fetch_overnight_news():
    url = "https://openapi.naver.com/v1/search/news.json"
    headers = {"X-Naver-Client-Id": Client_ID, "X-Naver-Client-Secret": Client_Secret}
    params = {"query": TARGET_NAME, "display": 100, "sort": "date"}
    
    now_kst = datetime.now(KST)
    yesterday_1530 = (now_kst - timedelta(days=1)).replace(hour=15, minute=30, second=0, microsecond=0)
    
    keywords = ['공급', '수주', '실적', '매출', '엔비디아', '계약']
    valid_news = []
    
    res = requests.get(url, headers=headers, params=params)
    if res.status_code == 200:
        for item in res.json().get('items', []):
            pub_date = parser.parse(item['pubDate']).astimezone(KST)
            if yesterday_1530 <= pub_date <= now_kst:
                title = item['title'].replace("<b>", "").replace("</b>", "").replace("&quot;", "")
                if any(kw in title for kw in keywords):
                    valid_news.append(title)
    return valid_news

def analyze_with_gemini(news_list):
    if not news_list: return 0, "유효한 호재성 키워드를 포함한 야간 뉴스가 없습니다."
    merged_news = "\n".join(news_list[:15])
    
    prompt = f"""
    너는 알고리즘 트레이딩을 위한 객관적인 퀀트 데이터 분석 시스템이다.
    타깃 종목은 '{TARGET_NAME}'이다. 이 종목은 글로벌 HBM 공급망의 핵심 병목 지점으로서 전방 산업의 수요가 즉각적인 재무 데이터로 직결되는 프록시(Proxy) 역할을 한다.
    우리의 투자 로직은 '야간에 발생한 확실한 내러티브(가치평가 요소)를 아침 시초가 갭 상승 모멘텀으로 번역'하는 것이다.
    
    다음 뉴스를 읽고, 데이터를 기반으로 가치평가를 수행(operationalize)하라.
    
    [엄격한 배제 규칙]
    - 뉴스의 수치, 호재, 계약 등이 '{TARGET_NAME}' 본체의 것인지, 아니면 관련 테마주나 하청업체(예: 장비사 등)의 것인지 엄격히 분리하라.
    - 호재의 주체가 '{TARGET_NAME}' 본체가 아니라면, 시스템 오작동을 막기 위해 철저히 무시하고 배제하라.
    - 원문에 없는 숫자를 절대 생성하지 마라.
    
    [가치평가 지침]
    1. 대상 및 제품: '{TARGET_NAME}'가 명확한 고객에게 무엇을 파는지 데이터로 확인되는가?
    2. 확인된 숫자: '{TARGET_NAME}' 본체의 실적, 계약 규모 등 명시적 수치가 있는가?
    3. 가치평가 연결: 이 뉴스가 '{TARGET_NAME}'의 Revenue, Margin, Reinvestment, Risk 요소 중 어디에 어떻게 긍정적 데이터를 제공하는가?
    4. 진입 합리성: 위 데이터를 종합할 때, 오늘 아침 시초가에 '{TARGET_NAME}'을 매수할 객관적인 데이터 근거가 성립하는가?
    
    위 기준에 따라 '{TARGET_NAME}' 본체의 직접적이고 명확한 상승 근거가 있을 때만 80~100점을 부여하고, 타 회사 수혜나 단순 기대감은 50점 미만으로 평가하라.
    분석 내용을 객관적으로 서술한 뒤, 맨 마지막 줄에 반드시 "[SCORE: 점수]" 형태로 출력하라.
    
    [뉴스 원문]
    {merged_news}
    """
    
    for attempt in range(3):
        try:
            response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
            text = response.text.strip()
            score = 0
            for line in text.split('\n'):
                if "[SCORE:" in line:
                    # 1. 태그 안의 문자열 추출
                    raw_score_str = line.split("[SCORE:")[1].replace("]", "").strip()
                    # 2. 정규식을 사용해 오직 숫자(0-9)만 추출 (별표, 띄어쓰기 등 완전 제거)
                    clean_score_str = re.sub(r'[^0-9]', '', raw_score_str)
                    
                    if clean_score_str:
                        score = int(clean_score_str)
                    break # 점수를 찾았으면 반복문 즉시 탈출
            return score, text
            
        except Exception as e:
            # 파이썬 내부 에러인지 통신 에러인지 명확히 로그에 남김
            print(f"⚠️ [Gemini API 분석] 오류 발생. {attempt+1}회 재시도 중... (사유: {e})")
            time.sleep(3)
            
    return 0, "API 오류 및 파싱 실패: 3회 재시도 초과"

def generate_post_trade_feedback(status, insight, rationale, return_rate):
    if status == "SKIP":
        prompt = f"너는 알고리즘 트레이딩 시스템의 리스크 평가 모듈이다. 오늘 시스템은 매수를 포기(SKIP)했다. [분석 데이터]: {insight} [필터값]: {rationale}. 시초가 갭 상승 모멘텀을 포착하는 시스템 로직을 기준으로, 수집된 데이터를 통해 매수를 보류한 결정이 리스크 관리 및 자본 보존 측면에서 왜 타당한지 객관적으로 3문장으로 분석하라."
    elif status == "FAIL":
        prompt = f"너는 알고리즘 트레이딩 시스템의 리스크 평가 모듈이다. 호재 판단으로 매수 대기 상태였으나 인프라 에러로 진입에 실패(FAIL)했다. [필터값]: {rationale}. 데이터 기반 투자 로직을 실제 시스템으로 조작화(operationalize)하는 과정에서 발생한 이러한 체결 미이행 리스크가, 퀀트 매매 전략 전체의 신뢰도와 기회비용에 미치는 구조적 영향에 대해 3문장으로 분석하라."
    else:
        # 상태에 따라 청산 원인(익절, 손절, 타임아웃)을 프롬프트에 명시
        exit_reason = ""
        if status == "TRADE_TP": exit_reason = f"+{TAKE_PROFIT_PCT}% 도달에 따른 기계적 익절(Take Profit)"
        elif status == "TRADE_SL": exit_reason = f"{STOP_LOSS_PCT}% 도달에 따른 기계적 손절(Stop Loss)"
        else: exit_reason = "15분 경과에 따른 타임아웃(Time-out) 청산"

        prompt = f"""
        너는 알고리즘 트레이딩 시스템의 매매 결과 평가 모듈이다. 오늘 매수 후 청산을 완료했다. 
        [사전 분석 데이터]: {insight}
        [청산 사유]: {exit_reason}
        [최종 청산 수익률]: {return_rate}%
        
        선정 종목에 대한 사전 가치평가 내러티브가 실제 시장 데이터인 수익률로 어떻게 검증되었는지, 그리고 {exit_reason}이라는 청산 트리거가 발동된 시장의 미시적 움직임(예: 차익실현 매물, 지지선 붕괴 등)에 대해 종합하여 객관적이고 비판적인 해석을 3문장으로 작성하라.
        """
        
    for attempt in range(3):
        try:
            response = client.models.generate_content(model="gemini-3-flash-preview", contents=prompt)
            return response.text.strip().replace('\n', ' ')
        except Exception as e:
            print(f"⚠️ [Gemini API 피드백] 통신 오류 발생. {attempt+1}회 재시도 중... (사유: {e})")
            time.sleep(3)
            
    return "피드백 생성 실패: 구글 서버 API 응답 한도 초과 또는 통신 지연"

def write_daily_trade_log(status):
    file_exists = os.path.isfile(LOG_FILE)
    with open(LOG_FILE, mode='a', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow([
                "일자", "상태", "매수주식", "사고과정", "판단근거", "매수시간", "매수단가", "매수량", 
                "매도시간", "매도단가", "매도량", "수익률(%)", "잔여현금", "AI_비판적피드백"
            ])
            
        rec = daily_trade_record
        feedback = generate_post_trade_feedback(status, rec['ai_insight'], rec['rationale'], rec['return_rate'])
        
        writer.writerow([
            rec['date'], status, TARGET_NAME, rec['ai_insight'], rec['rationale'],
            rec['buy_time'], rec['buy_price'], rec['buy_qty'],
            rec['sell_time'], rec['sell_price'], rec['sell_qty'],
            round(rec['return_rate'], 2), rec['current_cash'], feedback
        ])
    print(f"📝 [{datetime.now(KST).strftime('%H:%M:%S')}] {status} 로그 및 AI 피드백 CSV 저장 완료")

# --- [코어 스케줄러] ---
async def trading_scheduler():
    global daily_analysis_done, position_held, buy_qty, daily_trade_record
    
    while True:
        now = datetime.now(KST)
        
        # 1. 08:30 비정형 데이터 분석 및 필터 검증
        if now.hour == 8 and 45 <= now.minute < 55 and not daily_analysis_done:
            print(f"🔍 [{now.strftime('%H:%M:%S')}] 일일 분석 스케줄러 가동")
            soxx_ok, rsi_val, rsi_ok = await asyncio.to_thread(check_macro_and_tech_filters)
            news_list = await asyncio.to_thread(fetch_overnight_news)
            ai_score, ai_insight = await asyncio.to_thread(analyze_with_gemini, news_list)
            
            daily_trade_record = {k: v for k, v in daily_trade_record.items()} 
            daily_trade_record["date"] = now.strftime("%Y-%m-%d")
            daily_trade_record["ai_insight"] = ai_insight.replace('\n', ' ')
            daily_trade_record["rationale"] = f"SCORE:{ai_score}, SOXX_Up:{soxx_ok}, RSI:{rsi_val:.1f}"
            
            if ai_score >= 80 and soxx_ok and rsi_ok:
                daily_analysis_done = "BUY_READY"
                print(f"🚀 [매수 대기] 조건 충족 (스코어: {ai_score})")
            else:
                daily_analysis_done = "SKIP"
                daily_trade_record["status"] = "SKIP"
                await asyncio.to_thread(write_daily_trade_log, "SKIP")
                
        # 2. 09:01 ~ 09:05 시초가 진입 (네트워크 지연 대비 5분 재시도 윈도우)
        elif now.hour == 9 and 1 <= now.minute <= 5 and daily_analysis_done == "BUY_READY" and not position_held:
            curr_price = get_current_price()
            if curr_price > 0:
                buy_qty = STOCK_ALLOCATION // curr_price
                if buy_qty > 0 and send_kis_order("BUY", buy_qty):
                    position_held = True
                    daily_analysis_done = "BOUGHT"
                    daily_trade_record["status"] = "TRADE"
                    daily_trade_record["buy_time"] = now.strftime("%H:%M:%S")
                    daily_trade_record["buy_price"] = curr_price
                    daily_trade_record["buy_qty"] = buy_qty
                    daily_trade_record["current_cash"] -= (curr_price * buy_qty)
                    print(f"💰 [매수 완료] {TARGET_NAME} {buy_qty}주 진입 (매수가: {curr_price})")
                else:
                    print("⚠️ [주문 대기] 매수 주문 거부 혹은 수량 부족, 다음 사이클 재시도")
            else:
                print("⚠️ [현재가 0원] 가격 조회 실패로 매수 연기, 다음 사이클 재시도")
                    
        # 3. 매수 실패/누락 예외 처리 로직 (09:06 이후 대기 상태 에러 판정)
        elif now.hour == 9 and now.minute >= 6 and daily_analysis_done == "BUY_READY" and not position_held:
            daily_analysis_done = "FAIL"
            daily_trade_record["status"] = "FAIL"
            daily_trade_record["ai_insight"] = "매수 조건 충족했으나 주문 스케줄러 내 집행 실패 또는 인프라 에러 발생."
            await asyncio.to_thread(write_daily_trade_log, "FAIL")
            print("❌ [시스템 에러] 매수 진입 실패 판정 -> 예외 사유 CSV 적재 완료")
            
        # 🛡️ 4. 동적 리스크 관리 루틴 (익절/손절 체크: 09:01:30 ~ 09:14)
        elif now.hour == 9 and 1 <= now.minute < 15 and position_held:
            curr_price = get_current_price()
            if curr_price > 0:
                unrealized_return = ((curr_price - daily_trade_record["buy_price"]) / daily_trade_record["buy_price"]) * 100
                
                # 익절 또는 손절 조건 도달 시 즉각 청산
                if unrealized_return >= TAKE_PROFIT_PCT or unrealized_return <= STOP_LOSS_PCT:
                    if send_kis_order("SELL", buy_qty):
                        position_held = False
                        status_type = "TRADE_TP" if unrealized_return >= TAKE_PROFIT_PCT else "TRADE_SL"
                        
                        daily_trade_record["status"] = status_type
                        daily_trade_record["sell_time"] = now.strftime("%H:%M:%S")
                        daily_trade_record["sell_price"] = curr_price
                        daily_trade_record["sell_qty"] = buy_qty
                        daily_trade_record["return_rate"] = unrealized_return
                        daily_trade_record["current_cash"] += (curr_price * buy_qty)
                        
                        print(f"⚡ [강제 청산] 수익률 {unrealized_return:.2f}% 도달로 인한 {status_type} 실행")
                        await asyncio.to_thread(write_daily_trade_log, status_type)
                    
        # 5. 09:15 타임아웃 청산 (익절/손절에 도달하지 않고 15분 버틴 경우)
        elif now.hour == 9 and now.minute == 15 and position_held:
            sell_price = get_current_price()
            if send_kis_order("SELL", buy_qty):
                position_held = False
                daily_trade_record["status"] = "TRADE_TO" # Time Out
                daily_trade_record["sell_time"] = now.strftime("%H:%M:%S")
                daily_trade_record["sell_price"] = sell_price
                daily_trade_record["sell_qty"] = buy_qty
                
                return_rate = ((sell_price - daily_trade_record["buy_price"]) / daily_trade_record["buy_price"]) * 100
                daily_trade_record["return_rate"] = return_rate
                daily_trade_record["current_cash"] += (sell_price * buy_qty)
                
                print("💸 [매도 완료] 포지션 타임아웃 청산 및 종합 로그 작성 완료")
                await asyncio.to_thread(write_daily_trade_log, "TRADE_TO")
                
        # 자정 초기화
        elif now.hour == 0 and now.minute == 0:
            daily_analysis_done = False
            daily_trade_record = {
                "date": "", "status": "", "ai_insight": "", "rationale": "",
                "buy_time": "-", "buy_price": 0, "buy_qty": 0,
                "sell_time": "-", "sell_price": 0, "sell_qty": 0,
                "return_rate": 0.0, "current_cash": TOTAL_BUDGET
            }
            # 여기 있던 fetch_kis_token() 삭제 (자정 서버 점검 시간 회피)

        # 🔥 아침 8시 정각: 증권사 서버 점검이 끝난 안전한 시간에 토큰 발급
        elif now.hour == 8 and now.minute == 0:
            fetch_kis_token()
            await asyncio.sleep(60) # 1분 이내 중복 호출 방지
            
        await asyncio.sleep(30)

@app.on_event("startup")
async def start_bot():
    fetch_kis_token()
    asyncio.create_task(trading_scheduler())