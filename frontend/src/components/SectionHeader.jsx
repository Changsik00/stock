// 매매 탭 영역 공통 헤더 — 제목 + 배지(실주문=주황 경고 / 기록=회색 중립) + 한 줄 설명.
export default function SectionHeader({ title, desc, badge, tone = 'neutral', action = null }) {
  return (
    <header className="at-head">
      <div className="at-head-row">
        <h2 className="mt-title">{title}</h2>
        <span className={`badge at-badge ${tone === 'warn' ? 'badge-warn' : 'badge-neutral'}`}>{badge}</span>
        {action && <div className="at-head-action">{action}</div>}
      </div>
      {desc && <p className="at-desc">{desc}</p>}
    </header>
  )
}
