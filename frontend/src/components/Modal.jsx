import { useEffect } from 'react'

// 공용 모달 — 대시보드 탭(PLAN.md §6 3.7-1)에서 타일/행 클릭 시 차트·전체 리스트를
// "뒤로 숨기는" 용도로 쓴다. ESC 키와 배경(backdrop) 클릭으로 닫히고, 본문
// (modal-body)만 세로 스크롤한다 — 리스트가 100행 이상이어도 모달 밖으로 페이지가
// 늘어나지 않는다.
//
// 순수 프레젠테이션 컴포넌트다: 열림 상태(open)는 호출부가 소유한다(제어 컴포넌트).
// children은 호출부가 그대로 넘긴 JSX 엘리먼트 디스크립터일 뿐이라 open=false일 때
// (아래 `if (!open) return null`) React가 이를 리컨사일하지 않는다 — 즉 모달 전용
// 데이터 페칭 컴포넌트를 children으로 넘기면 그 컴포넌트의 useEffect는 모달이 실제로
// 열릴 때만(마운트 시) 실행되고, 닫히면 언마운트되어 다음에 열 때 다시 최신 데이터를
// 받아온다 — 별도 캐시/무효화 로직 없이 "열 때마다 새로 불러오기"가 자연히 성립한다.
export default function Modal({ open, onClose, title, children, maxWidth = 760 }) {
  useEffect(() => {
    if (!open) return undefined

    function onKeyDown(e) {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)

    // 배경 스크롤 잠금 — 모달이 떠 있는 동안 뒤 페이지가 함께 스크롤되지 않게 한다.
    const prevOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'

    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = prevOverflow
    }
  }, [open, onClose])

  if (!open) return null

  return (
    <div className="modal-backdrop" onClick={onClose} role="presentation">
      <div
        className="modal-panel"
        style={{ maxWidth }}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <div className="modal-header">
          <span className="modal-title">{title}</span>
          <button type="button" className="modal-close" onClick={onClose} aria-label="닫기">
            ×
          </button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  )
}
