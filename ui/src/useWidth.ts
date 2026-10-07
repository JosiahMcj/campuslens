import { useEffect, useRef, useState } from 'react'

/** The width an element has to draw in, followed as it changes. */
export function useWidth(): [React.RefObject<HTMLDivElement | null>, number] {
  const ref = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(640)
  useEffect(() => {
    const element = ref.current
    if (element === null) return
    const measure = () => {
      const next = Math.round(element.getBoundingClientRect().width)
      if (next > 0) setWidth(next)
    }
    measure()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(measure)
    observer.observe(element)
    return () => observer.disconnect()
  }, [])
  return [ref, width]
}
