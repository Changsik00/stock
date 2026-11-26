import { signedEokLabel } from './shared'

// 지금 유입 우세(PLAN.md §5.15) — Phase 5.128에서 대시보드 인라인 블록을 컴포넌트로 빼 시장 탭으로 옮겼다.
// 문구는 관찰+확률 서술만(명령형·추천형 금지), 신뢰도 낮은 조합은 숨기지 않고 "참고용 · 신호 약함"으로 표시.
// onKosdaq: '코스닥우세'일 때 '코스닥 보기' 버튼 동작(시장 탭에선 코스닥 탭 전환). 없으면 버튼을 숨긴다.
export default function RegimeCard({ regime, onKosdaq }) {
  if (!regime) return null
  return (
    <>
      <div className="section-title">지금 유입 우세</div>
      <div className={`regime-card regime-card-${regime.regime === '코스닥우세' ? 'kosdaq' : 'neutral'}`}>
        <div className="regime-card-top">
          <span className="regime-verdict">{regime.regime}</span>
          {regime.regime === '코스닥우세' && onKosdaq && (
            <button
              type="button"
              className="toggle-chip"
              onClick={onKosdaq}
              title="코스닥 탭으로 전환"
            >
              코스닥 보기 ›
            </button>
          )}
        </div>
        <div className="regime-reason">{regime.reason}</div>
        <div className="regime-combo-grid">
          {['kosdaq', 'kospi'].map((m) =>
            ['외국인', '기관계'].map((inv) => {
              const combo = regime[m]?.[inv]
              if (!combo) return null
              const streakLabel =
                combo.streak > 0
                  ? `${combo.streak}일 연속 매수`
                  : combo.streak < 0
                    ? `${Math.abs(combo.streak)}일 연속 매도`
                    : '연속 없음'
              return (
                <div
                  key={`${m}-${inv}`}
                  className={`regime-combo ${combo.reliable ? 'regime-combo-reliable' : 'regime-combo-weak'}`}
                >
                  <span className="regime-combo-label">
                    {m === 'kospi' ? '코스피' : '코스닥'} · {inv}
                  </span>
                  <span className="regime-combo-streak">{streakLabel}</span>
                  {combo.bucket_stats ? (
                    <span className="regime-combo-stats">
                      다음날 상승확률 {combo.bucket_stats.positive_rate_pct}% (표본 {combo.bucket_stats.n}일)
                    </span>
                  ) : (
                    <span className="regime-combo-stats">이 구간 과거 표본 없음</span>
                  )}
                  {!combo.reliable && <span className="regime-combo-hint">참고용 · 신호 약함</span>}
                  {/* 2026-07-23 수정 — 사용자 지적: "오늘 수급이 좋아 보이는데
                      왜 중립이냐". 확정 스트릭은 하루치 잠정 데이터로 방향을
                      안 뒤집는(보수적 처리) 게 맞지만, 오늘 실제로 반대 방향
                      움직임이 있다는 사실 자체는 숨기지 않는다. */}
                  {combo.live_reversal && (
                    <span className="regime-combo-hint regime-combo-reversal">
                      오늘 장중은 현재 {combo.today_live_net_value > 0 ? '매수' : '매도'} 전환 조짐
                    </span>
                  )}
                  {/* PLAN.md §5.17 — 수급 가속도(실시간 반응성 지표). 스트릭(위
                      streakLabel/bucket_stats, 느리지만 검증된 신호)과는 별도
                      필드로 항상 따로 보여준다 — 종합 판정에 섞지 않는다. 부호만
                      일관되게 해석 가능한 관찰 서술이고("가속"/"감속"), "좋다/
                      나쁘다" 판단 문구는 쓰지 않는다(§5 원칙). */}
                  {combo.acceleration ? (
                    <span className="regime-combo-hint regime-combo-reversal">
                      최근 30분 순매수 속도 {signedEokLabel(combo.acceleration.recent_velocity)}
                      (직전 30분 대비{' '}
                      {combo.acceleration.acceleration > 0
                        ? '가속'
                        : combo.acceleration.acceleration < 0
                          ? '감속'
                          : '속도 유지'}
                      )
                    </span>
                  ) : (
                    <span className="regime-combo-hint">가속도 데이터 부족(적립 중)</span>
                  )}
                </div>
              )
            })
          )}
        </div>
      </div>
    </>
  )
}
