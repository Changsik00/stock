import { useState } from 'react'
import Badge from '../Badge'
import { Delta, Skel } from './MetricTile'

// 이상 거래 카드(PLAN.md Phase 5.117) — 거래대금 급증 + 외인·기관 대량 수급 + 공매도 급증 중
// 두 조건 이상 충족한 종목을 매도 쏠림/매수 쏠림으로 나눠 보여 준다. 참고용 — 매매 신호 아님.
// 방향색(--up/--down)은 등락률·순매수 금액의 ▲▼에만 쓰고, 조건 칩·라벨은 중립색이다. 주황은 쓰지 않는다.

const num1 = new Intl.NumberFormat('ko-KR', { minimumFractionDigits: 1, maximumFractionDigits: 1 })
const num2 = new Intl.NumberFormat('ko-KR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const eok0 = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 0 })

const TABS = [
  { key: 'sell', label: '매도 쏠림' },
  { key: 'buy', label: '매수 쏠림' },
]

function mmdd(iso) {
  return iso ? iso.slice(5) : ''
}

// 백만원 → 억(정수). 1조 이상은 N.NN조. 부호는 +/−(U+2212).
function signedEok(million) {
  const sign = million < 0 ? '−' : '+'
  const abs = Math.abs(million)
  return abs >= 1e6 ? `${sign}${num2.format(abs / 1e6)}조` : `${sign}${eok0.format(abs / 100)}억`
}

function rateDir(v) {
  return v >= 0.005 ? 'up' : v <= -0.005 ? 'down' : ''
}

export default function FlowAnomalyCard({ data, loading, onRowClick }) {
  const [tab, setTab] = useState('sell')
  const [hintOpen, setHintOpen] = useState(false)
  const hasData = data && data.date
  const rows = hasData ? data[tab] || [] : []
  const th = data?.thresholds
  return (
    <>
      <div className="section-title mt-title vr-title">
        <span>
          이상 거래
          {hasData && (
            <span className="narr-meta">
              {data.provisional ? ` 장중 잠정 · ${data.as_of ?? '-'} 기준` : ` ${mmdd(data.date)} 기준`}
            </span>
          )}
        </span>
        {hasData && (
          <button type="button" className="btn btn-link btn-sm" aria-expanded={hintOpen} onClick={() => setHintOpen((v) => !v)}>
            {hintOpen ? '설명 닫기' : '설명'}
          </button>
        )}
      </div>
      {loading ? (
        <div className="vr-card">
          <Skel w={160} h={26} />
        </div>
      ) : !hasData ? (
        <div className="vr-card vr-state">데이터 없음</div>
      ) : (
        <div className="vr-card">
          <div className="fa-tabs">
            {TABS.map((t) => (
              <button
                key={t.key}
                type="button"
                className={`toggle-chip ${tab === t.key ? 'active' : ''}`}
                aria-pressed={tab === t.key}
                onClick={() => setTab(t.key)}
              >
                {t.label} {data[`${t.key}_total`] ?? 0}
              </button>
            ))}
          </div>
          {rows.length === 0 ? (
            <span className="vr-state">조건을 두 개 이상 충족한 {tab === 'sell' ? '매도' : '매수'} 쏠림 종목이 없습니다.</span>
          ) : (
            <div className="fa-list">
              {rows.map((r) => {
                const Tag = onRowClick ? 'button' : 'div'
                const c = r.conditions
                return (
                  <Tag
                    key={r.code}
                    type={onRowClick ? 'button' : undefined}
                    className="fa-row"
                    onClick={onRowClick ? () => onRowClick(r) : undefined}
                  >
                    <span className="fa-head">
                      <span className="fa-name">
                        <span className="fa-name-text">{r.name || r.code}</span>
                        {r.market && <Badge kind={r.market} />}
                      </span>
                      <span className="fa-amount">
                        <Delta dir={r.net_flow < 0 ? 'down' : 'up'}>{signedEok(r.net_flow)}</Delta>
                      </span>
                    </span>
                    <span className="fa-meta">
                      {typeof r.change_rate === 'number' && (
                        <Delta dir={rateDir(r.change_rate)}>
                          {r.change_rate < 0 ? '−' : '+'}
                          {num2.format(Math.abs(r.change_rate))}%
                        </Delta>
                      )}
                      <span>거래대금 {eok0.format(r.value / 100)}억 · {num1.format(r.value_ratio)}배</span>
                      <span>수급 비중 {eok0.format(r.flow_share * 100)}%</span>
                      <span>공매도 {r.short_ratio === null ? '판정 제외' : `${num1.format(r.short_ratio)}배`}</span>
                    </span>
                    <span className="fa-chips">
                      <span className={`fa-chip ${c.value_surge ? '' : 'fa-chip-off'}`}>{c.value_surge ? '거래대금 급증' : '거래대금 보통'}</span>
                      <span className={`fa-chip ${c.flow_large ? '' : 'fa-chip-off'}`}>{c.flow_large ? '외인·기관 대량' : '수급 보통'}</span>
                      <span className={`fa-chip ${c.short_surge ? '' : 'fa-chip-off'}`}>
                        {r.short_available ? (c.short_surge ? '공매도 급증' : '공매도 보통') : '공매도 제외'}
                      </span>
                    </span>
                  </Tag>
                )
              })}
            </div>
          )}
          <span className="vr-aside">참고용 — 매매 신호 아님 · 판정 {data.evaluated}종목</span>
          {hintOpen && (
            <p className="vr-foot">
              거래대금 급증은 오늘 거래대금이 직전 {th?.avg_days}거래일 평균의 {th?.value_surge_mult}배 이상, 외인·기관 대량은
              외국인+기관계 순매수의 절댓값이 거래대금의 {Math.round((th?.flow_share_min ?? 0) * 100)}% 이상이면서{' '}
              {eok0.format((th?.flow_abs_min_million ?? 0) / 100)}억 이상, 공매도 급증은 오늘 공매도 거래대금이 직전 평균의{' '}
              {th?.short_surge_mult}배 이상입니다. 조건을 {th?.min_conditions}개 이상 충족하면 후보이고, 순매수 부호로 매도·매수 쏠림을 나눕니다.
              정렬은 거래대금 대비 순매수 비중 내림차순입니다. 공매도는 오늘 값이나 평균이 없는 종목은 충족이 아니라 판정에서 제외합니다.
              임계값은 검증하지 않은 첫 배정값입니다. {data.provisional ? '장중 값은 잠정치이며 마감 후 달라질 수 있습니다. ' : ''}
              {data.scope_note} 참고용 — 매매 신호 아님.
            </p>
          )}
        </div>
      )}
    </>
  )
}
