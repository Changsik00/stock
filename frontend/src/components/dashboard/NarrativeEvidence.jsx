import { ALIGN, fmtMil, fmtRate, signCls } from './narrativeShared'

// 수급 대조(evidence) 한 줄 — 업종/테마 이름, 외국인·기관·개인 순매수(억) 또는 등락률, 일치/불일치 판정.
export default function EvidenceRow({ e }) {
  const v = ALIGN[e.verdict] || ALIGN.unknown
  return (
    <div className="narr-evidence-row">
      <span className="narr-ev-name">
        {e.role === 'beneficiary' ? '수혜' : '피해'} · {e.name}
        {e.market ? ` (${e.market === 'kospi' ? '코스피' : '코스닥'})` : e.group_type === 'theme' ? ' (테마)' : ' (업종)'}
      </span>
      {e.source === 'sector_flow' ? (
        <span className="narr-ev-vals">
          <span className="narr-ev-cell"><i>외</i><b className={signCls(e.foreign_net)}>{fmtMil(e.foreign_net)}</b></span>
          <span className="narr-ev-cell"><i>기</i><b className={signCls(e.institution_net)}>{fmtMil(e.institution_net)}</b></span>
          <span className="narr-ev-cell"><i>개</i><b className={signCls(e.individual_net)}>{fmtMil(e.individual_net)}</b></span>
        </span>
      ) : (
        <span className="narr-ev-vals"><span className="narr-ev-cell"><b className={signCls(e.change_rate)}>{fmtRate(e.change_rate)}</b></span></span>
      )}
      <span className={`narr-ev-verdict ${v.cls}`}>{e.verdict === 'unknown' ? '-' : v.label}</span>
    </div>
  )
}
