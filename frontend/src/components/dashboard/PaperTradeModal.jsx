import { useEffect, useState } from 'react'
import { closePaperTrade, createPaperTrade, deletePaperTrade, fetchPaperTrades } from '../../api'
import { numFmt, rateClass, rateLabel } from './shared'

// 가상 매매 기록(paper trade) 장부(PLAN.md §5.51) — §5.50 통합 뷰(호가·ETF 괴리율·
// 나스닥선물 등)로 판단 재료를 본 뒤 사용자가 직접 내린 진입/청산 결정을 진입가·
// 수량·청산가로 "기록"만 하고 손익을 계산해 주는 순수 로컬 장부. **이 모달은
// 실제 주문을 내지 않는다** — 백엔드가 kt10000/kt10001(§5.48)을 호출하지 않는다.
// house rule(§5): 손익 숫자는 그대로 보여주되 "사라"/"지금이 기회"/"강하다"/
// "약하다"/"유리하다" 같은 판단 문구는 절대 만들지 않는다.
//
// 대상 종목은 백엔드 `routers/markets.py::PAIR_SETS`(§5.50)의 6개 코드와 정확히
// 일치시킨 상수다 — 여기서 값이 어긋나면 400으로 튕겨나가므로 항상 백엔드와
// 짝을 맞춰 유지할 것.
const PAPER_TRADE_CODES = [
  { code: '005930', name: '삼성전자' },
  { code: '000660', name: 'SK하이닉스' },
  { code: '0193W0', name: 'KODEX 삼성전자레버리지' },
  { code: '0193T0', name: 'KODEX SK하이닉스레버리지' },
  { code: '0193L0', name: 'PLUS 삼성전자선물인버스2X' },
  { code: '0197X0', name: 'SOL SK하이닉스선물인버스2X' },
]

const paperTradeThStyle = {
  textAlign: 'left',
  padding: '4px 8px',
  borderBottom: '1px solid var(--border, #333)',
  whiteSpace: 'nowrap',
  fontWeight: 600,
}

const paperTradeTdStyle = {
  padding: '4px 8px',
  borderBottom: '1px solid var(--border, #333)',
  whiteSpace: 'nowrap',
}

// 백엔드가 주는 entry_at/exit_at은 ISO8601(UTC)이라 로컬 시각으로 변환해
// "MM/DD HH:MM"만 짧게 보여준다 — 이 장부 안에서만 쓰는 표기라 formatDate(날짜만
// 다루는 공용 유틸)와는 별개로 로컬 헬퍼를 둔다.
function formatTradeDateTime(iso) {
  if (!iso) return '-'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString('ko-KR', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

export default function PaperTradeModal() {
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const [code, setCode] = useState(PAPER_TRADE_CODES[0].code)
  const [side, setSide] = useState('buy')
  const [entryPrice, setEntryPrice] = useState('')
  const [entryQty, setEntryQty] = useState('')
  const [note, setNote] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [formError, setFormError] = useState(null)

  const [closingId, setClosingId] = useState(null)
  const [exitPriceInput, setExitPriceInput] = useState('')
  const [actionError, setActionError] = useState(null)

  const load = () => {
    setLoading(true)
    setError(null)
    return fetchPaperTrades('all')
      .then((body) => setRows(body.rows || []))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    load()
  }, [])

  const handleCreate = async (e) => {
    e.preventDefault()
    setFormError(null)
    const priceNum = Number(entryPrice)
    const qtyNum = Number(entryQty)
    if (!(priceNum > 0) || !(qtyNum > 0)) {
      setFormError('진입가/수량을 올바르게 입력하세요.')
      return
    }
    setSubmitting(true)
    try {
      await createPaperTrade({ code, side, entry_price: priceNum, entry_qty: qtyNum, note: note || undefined })
      setEntryPrice('')
      setEntryQty('')
      setNote('')
      await load()
    } catch (err) {
      setFormError(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  const handleCloseConfirm = async (id) => {
    setActionError(null)
    const priceNum = Number(exitPriceInput)
    if (!(priceNum > 0)) {
      setActionError('청산가를 올바르게 입력하세요.')
      return
    }
    try {
      await closePaperTrade(id, priceNum)
      setClosingId(null)
      setExitPriceInput('')
      await load()
    } catch (err) {
      setActionError(err.message)
    }
  }

  const handleDelete = async (id) => {
    setActionError(null)
    try {
      await deletePaperTrade(id)
      await load()
    } catch (err) {
      setActionError(err.message)
    }
  }

  const openRows = rows.filter((r) => r.status === 'open')
  const closedRows = rows.filter((r) => r.status === 'closed')

  return (
    <div>
      <div className="toggle-hint" style={{ marginBottom: 12 }}>
        이 기록은 참고용 장부이며 매매 신호가 아닙니다. 실제 주문은 별도로 진행해야 합니다.
      </div>

      <form
        onSubmit={handleCreate}
        style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'flex-end', marginBottom: 16 }}
      >
        <label style={{ display: 'flex', flexDirection: 'column', fontSize: 11, gap: 2 }}>
          종목
          <select value={code} onChange={(e) => setCode(e.target.value)}>
            {PAPER_TRADE_CODES.map((c) => (
              <option key={c.code} value={c.code}>
                {c.name} ({c.code})
              </option>
            ))}
          </select>
        </label>
        <label style={{ display: 'flex', flexDirection: 'column', fontSize: 11, gap: 2 }}>
          방향
          <select value={side} onChange={(e) => setSide(e.target.value)}>
            <option value="buy">매수(롱)</option>
            <option value="sell">매도(숏)</option>
          </select>
        </label>
        <label style={{ display: 'flex', flexDirection: 'column', fontSize: 11, gap: 2 }}>
          진입가
          <input
            type="number"
            step="0.01"
            min="0"
            value={entryPrice}
            onChange={(e) => setEntryPrice(e.target.value)}
            style={{ width: 100 }}
          />
        </label>
        <label style={{ display: 'flex', flexDirection: 'column', fontSize: 11, gap: 2 }}>
          수량
          <input
            type="number"
            step="0.0001"
            min="0"
            value={entryQty}
            onChange={(e) => setEntryQty(e.target.value)}
            style={{ width: 80 }}
          />
        </label>
        <label style={{ display: 'flex', flexDirection: 'column', fontSize: 11, gap: 2, flex: '1 1 160px' }}>
          메모(선택)
          <input type="text" value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} />
        </label>
        <button type="submit" className="toggle-chip" disabled={submitting}>
          {submitting ? '기록 중…' : '기록'}
        </button>
      </form>
      {formError && (
        <div className="state error" style={{ marginBottom: 12 }}>
          {formError}
        </div>
      )}
      {actionError && (
        <div className="state error" style={{ marginBottom: 12 }}>
          {actionError}
        </div>
      )}

      {loading && <div className="state">불러오는 중…</div>}
      {error && <div className="state error">{error}</div>}

      {!loading && !error && (
        <>
          <div style={{ fontWeight: 700, marginBottom: 6 }}>열린 포지션 ({openRows.length})</div>
          {openRows.length === 0 ? (
            <div className="toggle-hint" style={{ marginBottom: 16 }}>
              없음
            </div>
          ) : (
            <div className="table-scroll" style={{ overflowX: 'auto', marginBottom: 16 }}>
              <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
                <thead>
                  <tr>
                    <th style={paperTradeThStyle}>종목</th>
                    <th style={paperTradeThStyle}>방향</th>
                    <th style={paperTradeThStyle}>진입가</th>
                    <th style={paperTradeThStyle}>수량</th>
                    <th style={paperTradeThStyle}>진입시각</th>
                    <th style={paperTradeThStyle}>현재가</th>
                    <th style={paperTradeThStyle}>미실현손익</th>
                    <th style={paperTradeThStyle} />
                  </tr>
                </thead>
                <tbody>
                  {openRows.map((r) => (
                    <tr key={r.id}>
                      <td style={paperTradeTdStyle}>
                        {r.name} <span className="toggle-hint">{r.code}</span>
                      </td>
                      <td style={paperTradeTdStyle}>{r.side === 'buy' ? '매수(롱)' : '매도(숏)'}</td>
                      <td style={{ ...paperTradeTdStyle, textAlign: 'right' }}>{numFmt.format(r.entry_price)}</td>
                      <td style={{ ...paperTradeTdStyle, textAlign: 'right' }}>{numFmt.format(r.entry_qty)}</td>
                      <td style={paperTradeTdStyle}>{formatTradeDateTime(r.entry_at)}</td>
                      <td style={{ ...paperTradeTdStyle, textAlign: 'right' }}>
                        {r.current_price != null ? numFmt.format(r.current_price) : '-'}
                      </td>
                      <td
                        style={{ ...paperTradeTdStyle, textAlign: 'right' }}
                        className={rateClass(r.unrealized_pnl_pct)}
                      >
                        {r.unrealized_pnl != null ? `${numFmt.format(r.unrealized_pnl)}원` : '-'} (
                        {rateLabel(r.unrealized_pnl_pct)})
                      </td>
                      <td style={paperTradeTdStyle}>
                        {closingId === r.id ? (
                          <span style={{ display: 'flex', gap: 4 }}>
                            <input
                              type="number"
                              step="0.01"
                              min="0"
                              placeholder="청산가"
                              value={exitPriceInput}
                              onChange={(e) => setExitPriceInput(e.target.value)}
                              style={{ width: 80 }}
                            />
                            <button type="button" className="toggle-chip" onClick={() => handleCloseConfirm(r.id)}>
                              확정
                            </button>
                            <button
                              type="button"
                              className="toggle-chip"
                              onClick={() => {
                                setClosingId(null)
                                setExitPriceInput('')
                              }}
                            >
                              취소
                            </button>
                          </span>
                        ) : (
                          <button
                            type="button"
                            className="toggle-chip"
                            onClick={() => {
                              setClosingId(r.id)
                              setExitPriceInput('')
                              setActionError(null)
                            }}
                          >
                            청산 기록
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <div style={{ fontWeight: 700, marginBottom: 6 }}>닫힌 포지션 ({closedRows.length})</div>
          {closedRows.length === 0 ? (
            <div className="toggle-hint">없음</div>
          ) : (
            <div className="table-scroll" style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
                <thead>
                  <tr>
                    <th style={paperTradeThStyle}>종목</th>
                    <th style={paperTradeThStyle}>방향</th>
                    <th style={paperTradeThStyle}>진입가</th>
                    <th style={paperTradeThStyle}>청산가</th>
                    <th style={paperTradeThStyle}>수량</th>
                    <th style={paperTradeThStyle}>청산시각</th>
                    <th style={paperTradeThStyle}>실현손익</th>
                    <th style={paperTradeThStyle} />
                  </tr>
                </thead>
                <tbody>
                  {closedRows.map((r) => (
                    <tr key={r.id}>
                      <td style={paperTradeTdStyle}>
                        {r.name} <span className="toggle-hint">{r.code}</span>
                      </td>
                      <td style={paperTradeTdStyle}>{r.side === 'buy' ? '매수(롱)' : '매도(숏)'}</td>
                      <td style={{ ...paperTradeTdStyle, textAlign: 'right' }}>{numFmt.format(r.entry_price)}</td>
                      <td style={{ ...paperTradeTdStyle, textAlign: 'right' }}>
                        {r.exit_price != null ? numFmt.format(r.exit_price) : '-'}
                      </td>
                      <td style={{ ...paperTradeTdStyle, textAlign: 'right' }}>{numFmt.format(r.entry_qty)}</td>
                      <td style={paperTradeTdStyle}>{formatTradeDateTime(r.exit_at)}</td>
                      <td style={{ ...paperTradeTdStyle, textAlign: 'right' }} className={rateClass(r.realized_pnl_pct)}>
                        {r.realized_pnl != null ? `${numFmt.format(r.realized_pnl)}원` : '-'} (
                        {rateLabel(r.realized_pnl_pct)})
                      </td>
                      <td style={paperTradeTdStyle}>
                        <button type="button" className="toggle-chip" onClick={() => handleDelete(r.id)}>
                          삭제
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  )
}
