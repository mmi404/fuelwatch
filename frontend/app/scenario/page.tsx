'use client';

import SystemHealth from '@/components/health/SystemHealth';
import AutopilotPanel from '@/components/scenario/AutopilotPanel';
import DemoScript from '@/components/scenario/DemoScript';
import DisruptionTimeline from '@/components/scenario/DisruptionTimeline';
import SimControl from '@/components/scenario/SimControl';
import s from '@/components/scenario/Scenario.module.css';

/** Drives a demonstration: the simulator's controls, the autopilot, the disruptions in the world and a short script. */
export default function ScenarioPage() {
  return (
    <div className={s.page}>
      <h1 className="sr-only">Scenario: demo controls</h1>
      <div className={s.col}>
        <SimControl />
        <SystemHealth />
        <DisruptionTimeline />
      </div>
      <div className={s.col}>
        <AutopilotPanel />
        <DemoScript />
      </div>
    </div>
  );
}
