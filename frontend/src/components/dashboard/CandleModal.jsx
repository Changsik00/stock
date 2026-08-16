import { useEffect, useState } from 'react'
import { STATIC_DATA, fetchMarketIntraday, fetchMarketSeries } from '../../api'
import CandleChart from '../CandleChart'
import PeriodPicker from '../PeriodPicker'
import { INTRADAY_OPTIONS, MARKETS } from '../../constants'
import { formatDate } from '../../format'
import { DEFAULT_CANDLE_DAYS } from './shared'

// ---------------------------------------------------------------------------
// 모달 본문 컴포넌트 — Modal이 open=false일 때 렌더 트리에서 완전히 빠지므로(Modal.jsx
// 주석 참고) 아래 컴포넌트들은 "마운트될 때(=모달이 열릴 때)"만 데이터를 불러온다.
// MarketPage.jsx의 동일 섹션 로직을 모달 안에서 재현한 것으로, 백엔드 응답 스키마·
// 상태 관리 패턴은 그대로 가져왔다(중복이지만 페이지 단위로 완전히 분리하라는
// 작업 지시에 따라 MarketPage를 import하지 않고 이 파일 안에서 자기완결로 둔다).
// ---------------------------------------------------------------------------

// 지수 타일 클릭 시 뜨는 캔들 모달 — 분봉 토글(PLAN.md §5.5-1) 추가 전에는 90일
// EOD만 보여줘 다른 수급 모달들(1D 기본)과 기본값이 어긋나 있었다. MarketPage.jsx의
// intradayMode 토글과 동일한 패턴을 이식한다(코드 재사용보다 최소 침습 이식을
// 선택 — 작업 지시 참고). 기본값은 1분(§5.5-1), 선물은 분봉 소스가 없어
// (routers/markets.py 501) 'daily'로 강제, 정적 배포(STATIC_DATA)도 실시간
// 온디맨드 소스가 없어 'daily'로 시작하고 토글 UI 자체를 숨긴다.
export default function CandleModal({ market }) {
  const label = MARKETS.find((m) => m.key === market)?.label || market
  const [intradayMode, setIntradayMode] = useState(STATIC_DATA || market === 'futures' ? 'daily' : 1)
  const [days, setDays] = useState(DEFAULT_CANDLE_DAYS)
  const [prices, setPrices] = useState(null)
  const [volumeProfile, setVolumeProfile] = useState(null)
  const [flowProfile, setFlowProfile] = useState(null)
  // 선물 순매매 프로파일(PLAN.md §5.41)은 외국인/기관계 둘 다 한 번에 그리면
  // 레벨 선이 최대 16개(방향당 4개 x 2방향 x 2투자자)까지 늘어나 캔들 위가
  // 지나치게 복잡해진다 — 기본은 외국인만 보여주고, 토글로 기관계를 볼 수
  // 있게 한다(동시 렌더 대신 선택형, 사용자 판단으로 전환).
  const [flowInvestor, setFlowInvestor] = useState('외국인')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const [intradayBars, setIntradayBars] = useState([])
  const [intradayDate, setIntradayDate] = useState(null)
  const [intradayLoading, setIntradayLoading] = useState(false)
  const [intradayError, setIntradayError] = useState(null)

  // 선물엔 분봉 옵션이 없다 — 혹시라도 futures 모달이 분봉 모드로 남아 있으면
  // 되돌린다(MarketPage.jsx와 동일한 안전장치).
  useEffect(() => {
    if (market === 'futures' && intradayMode !== 'daily') setIntradayMode('daily')
  }, [market, intradayMode])

  useEffect(() => {
    if (STATIC_DATA || intradayMode === 'daily' || market === 'futures') return undefined
    let cancelled = false
    setIntradayLoading(true)
    setIntradayError(null)
    fetchMarketIntraday(market, intradayMode)
      .then((body) => {
        if (!cancelled) {
          setIntradayBars(body.bars || [])
          setIntradayDate(body.date)
        }
      })
      .catch((e) => {
        if (!cancelled) setIntradayError(e.message)
      })
      .finally(() => {
        if (!cancelled) setIntradayLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [market, intradayMode])

  useEffect(() => {
    if (intradayMode !== 'daily') return undefined
    let cancelled = false
    setLoading(true)
    setError(null)
    // includeVolumeProfile=true — 일봉 캔들에 지지/저항 후보 참조선(PLAN.md §5.34)을
    // 그리기 위함. includeFlowProfile은 market='futures'일 때만 의미가 있고(PLAN.md
    // §5.41), 다른 market에 줘도 백엔드가 무시하므로 항상 넘겨도 안전하다. STATIC_DATA
    // 경로는 이 필드들을 애초에 안 주므로 undefined로 안전하게 떨어진다(api.js
    // fetchMarketSeries 주석 참고).
    fetchMarketSeries(market, days, true, market === 'futures')
      .then((body) => {
        if (!cancelled) {
          setPrices(body.prices || [])
          setVolumeProfile(body.volume_profile || null)
          setFlowProfile(body.flow_profile || null)
        }
      })
      .catch((e) => {
        if (!cancelled) setError(e.message)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [market, days, intradayMode])

  // CandleChart의 `levels` prop 형태({price, label, isPoc})로 변환(StockDetailModal.jsx와
  // 동일한 매핑). 표본 부족/데이터 없음이면 volumeProfile.levels가 빈 배열(quant/
  // volume_profile.py 참고) — 항상 배열만 만든다(undefined 방지).
  // §5.34(중립 지지/저항선)와 §5.41(방향성 순매매 레벨)은 개념이 달라 함께 켤 수도
  // 있지만, 선물 캔들은 이미 §5.41 flowLevels가 방향성 있는 핵심 정보라 §5.34
  // levels는 선물에는 배선하지 않는다(선이 너무 많아지는 것을 피하기 위한 판단 —
  // 코스피/코스닥은 §5.41 대상이 아니므로 기존처럼 levels를 그대로 쓴다).
  const volumeProfileLevels =
    market === 'futures'
      ? []
      : (volumeProfile?.levels || []).map((lv) => ({
          price: lv.price_mid,
          isPoc: lv.is_poc,
          label: lv.is_poc ? '거래량 최다 구간(POC, 근사)' : '거래량 집중 구간(근사)',
        }))

  // CandleChart의 `flowLevels` prop 형태({price, side, label})로 변환 — 선택된
  // 투자자(flowInvestor)의 buy_levels/sell_levels만 그린다(컴포넌트 상단 state
  // 주석 참고). 문구는 관찰형만("~순매수/순매도가 몰렸다", 근사 명시) — "매수/매도
  // 신호"로 읽힐 수 있는 표현은 쓰지 않는다.
  const selectedFlowProfile = flowProfile?.[flowInvestor]
  const flowLevels = [
    ...(selectedFlowProfile?.buy_levels || []).map((lv) => ({
      price: lv.price_mid,
      side: 'buy',
      label: `${flowInvestor} 순매수 집중 구간(근사)`,
    })),
    ...(selectedFlowProfile?.sell_levels || []).map((lv) => ({
      price: lv.price_mid,
      side: 'sell',
      label: `${flowInvestor} 순매도 집중 구간(근사)`,
    })),
  ]

  return (
    <div>
      <div className="toggle-hint" style={{ marginBottom: 8 }}>
        {label} · 캔들 + 거래량
      </div>

      {!STATIC_DATA && (
        <div className="toggle-row">
          {INTRADAY_OPTIONS.map((opt) => {
            const disabled = market === 'futures' && opt.key !== 'daily'
            return (
              <button
                key={opt.key}
                type="button"
                className={`toggle-chip ${intradayMode === opt.key ? 'active' : ''}`}
                disabled={disabled}
                title={disabled ? 'K200 선물은 분봉 데이터 소스가 없습니다' : undefined}
                onClick={() => setIntradayMode(opt.key)}
              >
                {opt.label}
              </button>
            )
          })}
          <span className="toggle-hint">
            {intradayMode === 'daily' ? '분봉은 오늘 하루치만 제공' : '오늘 하루치 · 참고용'}
          </span>
        </div>
      )}

      {intradayMode === 'daily' && (
        <>
          <PeriodPicker value={days} onChange={setDays} />
          {/* 선물 순매매 프로파일(PLAN.md §5.41) 투자자 토글 — 외국인/기관계
              동시 렌더는 선이 너무 많아져(방향당 최대 4개 x 2방향 x 2투자자)
              하나씩만 보여주고 전환하게 한다(컴포넌트 상단 flowInvestor state
              주석 참고). */}
          {market === 'futures' && (
            <div className="toggle-row">
              {['외국인', '기관계'].map((inv) => (
                <button
                  key={inv}
                  type="button"
                  className={`toggle-chip ${flowInvestor === inv ? 'active' : ''}`}
                  onClick={() => setFlowInvestor(inv)}
                >
                  {inv} 순매매 프로파일
                </button>
              ))}
              <span className="toggle-hint">
                하루 단위 순매매 금액을 그날 저가~고가에 균등분배한 근사 · 미결제약정(포지션) 아님 · 매매 신호 아님
              </span>
            </div>
          )}
          {loading && <div className="state">불러오는 중…</div>}
          {error && <div className="state error">{error}</div>}
          {!loading && !error && prices && prices.length > 0 && (
            <CandleChart data={prices} height={320} levels={volumeProfileLevels} flowLevels={flowLevels} />
          )}
          {/* PLAN.md §5.21-3 — 선물은 분봉이 없어 일봉이 유일한 뷰라, 마지막 봉이
              basis/live의 오늘 잠정치(provisional)로 채워졌을 때 확정치가 아님을
              명시한다(§5 "관찰 사실만" 원칙 — 확정치처럼 보이면 안 됨). */}
          {!loading && !error && prices && prices.length > 0 && prices[prices.length - 1]?.provisional && (
            <div className="toggle-hint" style={{ marginTop: 4 }}>
              마지막 봉은 장중 잠정치 · 체결마다 갱신, 확정 아님
            </div>
          )}
          {!loading && !error && prices && prices.length === 0 && (
            <div className="state">해당 기간에 표시할 데이터가 없습니다.</div>
          )}
        </>
      )}

      {intradayMode !== 'daily' && (
        <>
          {intradayLoading && <div className="state">불러오는 중…</div>}
          {intradayError && <div className="state error">{intradayError}</div>}
          {!intradayLoading && !intradayError && intradayBars.length === 0 && (
            <div className="state">오늘 분봉 데이터가 없습니다(장 시작 전이거나 휴장일 수 있음).</div>
          )}
          {!intradayLoading && !intradayError && intradayBars.length > 0 && (
            <CandleChart
              key={`${market}-${intradayMode}`}
              data={intradayBars}
              intraday
              height={320}
              title={`캔들 · 거래량 (${intradayMode}분봉 · ${formatDate(intradayDate)})`}
            />
          )}
        </>
      )}
    </div>
  )
}
