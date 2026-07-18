import { CandlestickSeries, HistogramSeries, createChart } from 'lightweight-charts'
import { useEffect, useMemo, useRef } from 'react'
import { formatDate } from '../format'

const numFmt = new Intl.NumberFormat('ko-KR')

// 크로스헤어 범례용 % 표기: 등락률은 부호 포함 소수 2자리, 변동폭은 소수 1자리.
function fmtSignedPct(v) {
  return `${v > 0 ? '+' : ''}${v.toFixed(2)}%`
}

// "YYYYMMDD" 또는 "YYYY-MM-DD" -> lightweight-charts가 요구하는 "YYYY-MM-DD".
function toLwcTime(d) {
  const digits = String(d).replaceAll('-', '')
  return `${digits.slice(0, 4)}-${digits.slice(4, 6)}-${digits.slice(6, 8)}`
}

// index.css의 CSS 변수를 읽어 lightweight-charts(캔버스, CSS var() 미지원)에 실제 색상값으로
// 넘긴다 — 라이트/다크 두 테마 모두 대응하려면 var() 문자열이 아닌 계산된 색이 필요하다.
function readCssVars(names) {
  const style = getComputedStyle(document.documentElement)
  return Object.fromEntries(names.map((n) => [n, style.getPropertyValue(n).trim()]))
}

const VAR_NAMES = ['--up', '--down', '--surface', '--grid', '--axis', '--text-muted', '--text-primary', '--border']

// lightweight-charts 기반 캔들 + 거래량 히스토그램 (PLAN.md §5.1 CandleChart.jsx,
// §5.4 색상 규칙: 상승/양봉=빨강(--up), 하락/음봉=파랑(--down)). 시장 탭(지수)과 향후
// 종목 탭(Phase 2-4)에서 재사용할 수 있도록 데이터 배열 + 높이만 props로 받는 일반화된
// 컴포넌트로 둔다. 캔들의 상승/하락은 당일 시가 대비 종가(표준 캔들 정의)로 판단하고,
// 거래량 바도 같은 날의 방향을 그대로 따른다.
//
// data: [{ date: "YYYYMMDD", open, high, low, close, volume }, ...] (오름차순)
export default function CandleChart({ data, height = 360, volumeHeightRatio = 0.22 }) {
  const containerRef = useRef(null)
  const chartRef = useRef(null)
  const candleSeriesRef = useRef(null)
  const volumeSeriesRef = useRef(null)
  const legendRef = useRef(null)
  // time("YYYY-MM-DD") -> { changeRate, rangeRate } — 크로스헤어 범례에서 등락률·변동폭을
  // 찾는 용도. updateLegend는 마운트 시 1회 만든 클로저라 ref로 최신 데이터를 넘긴다.
  const legendMetaRef = useRef(new Map())

  const points = useMemo(
    () =>
      (data || [])
        .filter((d) => d.open != null && d.high != null && d.low != null && d.close != null)
        .map((d) => ({
          time: toLwcTime(d.date),
          open: d.open,
          high: d.high,
          low: d.low,
          close: d.close,
          volume: d.volume ?? 0,
          changeRate: typeof d.changeRate === 'number' ? d.changeRate : null,
        })),
    [data]
  )

  // 차트 생성은 마운트 시 1회만 — 데이터/리사이즈는 별도 effect에서 갱신한다.
  useEffect(() => {
    const container = containerRef.current
    if (!container) return undefined

    const vars = readCssVars(VAR_NAMES)

    const chart = createChart(container, {
      autoSize: true,
      layout: {
        background: { color: vars['--surface'] || '#fff' },
        textColor: vars['--text-muted'] || '#898781',
        fontFamily: 'inherit',
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: vars['--grid'] || '#e1e0d9' },
        horzLines: { color: vars['--grid'] || '#e1e0d9' },
      },
      rightPriceScale: { borderColor: vars['--axis'] || '#c3c2b7' },
      timeScale: { borderColor: vars['--axis'] || '#c3c2b7', timeVisible: false },
      crosshair: { mode: 0 },
    })
    chartRef.current = chart

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: vars['--up'],
      downColor: vars['--down'],
      borderUpColor: vars['--up'],
      borderDownColor: vars['--down'],
      wickUpColor: vars['--up'],
      wickDownColor: vars['--down'],
      priceScaleId: 'right',
    })
    candleSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.06, bottom: volumeHeightRatio + 0.04 },
    })
    candleSeriesRef.current = candleSeries

    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
    })
    chart.priceScale('volume').applyOptions({
      scaleMargins: { top: 1 - volumeHeightRatio, bottom: 0 },
    })
    volumeSeriesRef.current = volumeSeries

    const legend = legendRef.current

    function updateLegend(bar) {
      if (!legend) return
      if (!bar) {
        legend.style.visibility = 'hidden'
        return
      }
      legend.style.visibility = 'visible'
      const up = bar.close >= bar.open
      const meta = legendMetaRef.current.get(bar.time)
      // 전일 대비 등락률 — 상승 빨강(--up)/하락 파랑(--down), 첫 봉 등 계산 불가 시 생략.
      let rateHtml = ''
      if (meta && meta.changeRate != null) {
        const attr =
          meta.changeRate > 0
            ? 'class="up"'
            : meta.changeRate < 0
              ? 'class="down"'
              : 'style="color:var(--text-secondary);font-weight:400"' // 보합은 중립색
        rateHtml = ` <span ${attr}>(${fmtSignedPct(meta.changeRate)})</span>`
      }
      // 당일 변동폭 (고가-저가)/전일종가 — 방향이 아닌 진폭이라 중립색.
      const rangeHtml = meta && meta.rangeRate != null ? `<span>변동 ${meta.rangeRate.toFixed(1)}%</span>` : ''
      legend.innerHTML =
        `<span>${formatDate(bar.time)}</span>` +
        `<span>시 ${numFmt.format(bar.open)}</span>` +
        `<span>고 ${numFmt.format(bar.high)}</span>` +
        `<span>저 ${numFmt.format(bar.low)}</span>` +
        `<span class="${up ? 'up' : 'down'}">종 ${numFmt.format(bar.close)}${rateHtml}</span>` +
        `<span>량 ${numFmt.format(bar.volume)}</span>` +
        rangeHtml
    }

    chart.subscribeCrosshairMove((param) => {
      const bar = param?.time ? param.seriesData?.get(candleSeries) : null
      if (bar) {
        const vol = param.seriesData?.get(volumeSeries)
        updateLegend({ ...bar, time: param.time, volume: vol?.value ?? 0 })
      } else {
        updateLegend(null)
      }
    })

    // 다크/라이트 전환(OS 설정) 시 캔들·거래량 색을 다시 계산해 반영한다.
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    const onThemeChange = () => {
      const v = readCssVars(VAR_NAMES)
      chart.applyOptions({
        layout: { background: { color: v['--surface'] }, textColor: v['--text-muted'] },
        grid: { vertLines: { color: v['--grid'] }, horzLines: { color: v['--grid'] } },
        rightPriceScale: { borderColor: v['--axis'] },
        timeScale: { borderColor: v['--axis'] },
      })
      candleSeries.applyOptions({
        upColor: v['--up'],
        downColor: v['--down'],
        borderUpColor: v['--up'],
        borderDownColor: v['--down'],
        wickUpColor: v['--up'],
        wickDownColor: v['--down'],
      })
    }
    media.addEventListener('change', onThemeChange)

    return () => {
      media.removeEventListener('change', onThemeChange)
      chart.remove()
      chartRef.current = null
      candleSeriesRef.current = null
      volumeSeriesRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 차트 인스턴스는 마운트 시 1회만 생성
  }, [])

  // 데이터 갱신 (기간/시장 변경 시).
  useEffect(() => {
    const candleSeries = candleSeriesRef.current
    const volumeSeries = volumeSeriesRef.current
    if (!candleSeries || !volumeSeries) return

    // 범례용 등락률·변동폭 사전 계산. changeRate 필드가 없으면 직전 봉 종가로 계산하고
    // (첫 봉은 생략), 변동폭은 (고-저)/전일종가 — 전일종가가 없으면 시가 기준.
    const meta = new Map()
    let prevClose = null
    for (const p of points) {
      let changeRate = p.changeRate
      if (changeRate == null && prevClose > 0) changeRate = ((p.close - prevClose) / prevClose) * 100
      const rangeBase = prevClose > 0 ? prevClose : p.open
      const rangeRate = rangeBase > 0 ? ((p.high - p.low) / rangeBase) * 100 : null
      meta.set(p.time, { changeRate: changeRate ?? null, rangeRate })
      prevClose = p.close
    }
    legendMetaRef.current = meta

    const vars = readCssVars(['--up', '--down'])
    candleSeries.setData(points.map(({ time, open, high, low, close }) => ({ time, open, high, low, close })))
    volumeSeries.setData(
      points.map((p) => ({
        time: p.time,
        value: p.volume,
        color: p.close >= p.open ? vars['--up'] : vars['--down'],
      }))
    )
    chartRef.current?.timeScale().fitContent()
  }, [points])

  return (
    <div className="chart-card candle-chart-card">
      <div className="chart-title">캔들 · 거래량</div>
      <div className="candle-chart-wrap" style={{ height }}>
        <div ref={legendRef} className="candle-legend" />
        <div ref={containerRef} className="candle-chart-container" />
      </div>
    </div>
  )
}
