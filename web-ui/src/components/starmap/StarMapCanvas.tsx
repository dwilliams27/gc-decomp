import { useStarAnimation } from "./useStarAnimation";

export function StarMapCanvas() {
  const canvasRef = useStarAnimation();

  return (
    <canvas
      ref={canvasRef}
      className="fixed inset-0 w-full h-full"
    />
  );
}
