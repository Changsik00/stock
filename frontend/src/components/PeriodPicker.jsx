import { PERIOD_OPTIONS } from '../constants'

// 1M/3M/6M/1Y/3Y 기간 선택 — 시장·매크로 페이지 공용 (PLAN.md §5.1 PeriodPicker.jsx).
export default function PeriodPicker({ value, onChange, options = PERIOD_OPTIONS }) {
  return (
    <div className="ranges">
      {options.map((o) => (
        <button
          key={o.key}
          type="button"
          className={`range ${value === o.key ? 'active' : ''}`}
          onClick={() => onChange(o.key)}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}
