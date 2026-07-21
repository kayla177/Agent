"use client";
import { useEffect } from "react";

export default function PlanetTheme({ planet }: { planet: string }) {
  useEffect(() => {
    const prev = document.body.dataset.planet;
    document.body.dataset.planet = planet;
    return () => { document.body.dataset.planet = prev ?? "earth"; };
  }, [planet]);
  return null;
}
