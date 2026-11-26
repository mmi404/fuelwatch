'use client';

import AlertFeed from '@/components/alerts/AlertFeed';
import CoverBoard from '@/components/cover/CoverBoard';
import PairDetail from '@/components/detail/PairDetail';
import HeroStrip from '@/components/hero/HeroStrip';
import NetworkMap from '@/components/network/NetworkMap';
import ProposalQueue from '@/components/proposals/ProposalQueue';
import './overview.css';

/** Overview: hero strip across the top, the ranked cover board and the network on the left, the decisions on the right. */
export default function Overview() {
  return (
    <div className="overview">
      <div className="overview-hero"><HeroStrip /></div>
      <div className="overview-main">
        <CoverBoard />
        <NetworkMap />
      </div>
      <aside className="overview-side">
        <ProposalQueue />
        <AlertFeed />
      </aside>
      <PairDetail />
    </div>
  );
}
