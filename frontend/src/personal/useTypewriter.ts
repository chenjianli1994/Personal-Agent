import { useCallback, useEffect, useRef, useState } from "react";

export function useTypewriter(full: string, enabled: boolean, charsPerTick = 2) {
  const [shown, setShown] = useState(enabled ? "" : full);
  const animationFrameRef = useRef<number>();

  useEffect(() => {
    const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (!enabled || reduceMotion) {
      setShown(full);
      return;
    }
    setShown("");
    let index = 0;
    const step = () => {
      index = Math.min(full.length, index + charsPerTick);
      setShown(full.slice(0, index));
      if (index < full.length) {
        animationFrameRef.current = requestAnimationFrame(step);
      }
    };
    animationFrameRef.current = requestAnimationFrame(step);
    return () => {
      if (animationFrameRef.current) cancelAnimationFrame(animationFrameRef.current);
    };
  }, [charsPerTick, enabled, full]);

  const skip = useCallback(() => {
    if (animationFrameRef.current) cancelAnimationFrame(animationFrameRef.current);
    setShown(full);
  }, [full]);

  return {
    shown,
    done: shown.length >= full.length,
    skip,
  };
}
