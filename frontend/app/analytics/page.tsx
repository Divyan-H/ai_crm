"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { toast } from "sonner";
import {
  Sparkles,
  BarChart3,
  TrendingUp,
  Percent,
  Mail,
  Zap,
  Loader2,
  Calendar,
} from "lucide-react";

export default function AnalyticsPage() {
  const [campaigns, setCampaigns] = useState<any[]>([]);
  const [channels, setChannels] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function loadAnalytics() {
      try {
        const campData = await api.analytics.campaigns();
        setCampaigns(campData);

        const chanData = await api.analytics.channels();
        setChannels(chanData);
      } catch (err: any) {
        console.error("Error loading analytics data", err);
        toast.error(err?.message ?? "Could not load analytics.");
      } finally {
        setLoading(false);
      }
    }
    loadAnalytics();
  }, []);

  return (
    <div className="p-8 space-y-8 max-w-7xl mx-auto">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-extrabold tracking-tight bg-gradient-to-r from-white via-slate-100 to-slate-400 bg-clip-text text-transparent">
          Analytics
        </h1>
        <p className="text-slate-400 text-sm mt-1">
          Historical overview of channels, open rates, and click metrics.
        </p>
      </div>

      {loading ? (
        <div className="flex flex-col items-center justify-center h-64 gap-2">
          <Loader2 className="w-8 h-8 animate-spin text-purple-500" />
          <span className="text-slate-400 text-sm">Aggregating historical metrics...</span>
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
          {/* Campaign Performance list */}
          <div className="lg:col-span-2 p-6 rounded-xl border border-slate-800 bg-slate-900/20 space-y-6">
            <h3 className="font-bold text-white text-lg flex items-center gap-2">
              <TrendingUp className="w-5 h-5 text-purple-400" />
              Top Performing Campaigns
            </h3>

            <div className="space-y-4">
              {campaigns.map((camp) => (
                <div
                  key={camp.id}
                  className="p-4 rounded-lg bg-slate-950/60 border border-slate-800 flex flex-col sm:flex-row justify-between gap-4 items-start sm:items-center"
                >
                  <div>
                    <span className="font-semibold text-slate-200 text-sm block">
                      {camp.name}
                    </span>
                    <span className="text-xs text-slate-500 capitalize">
                      {camp.channel} • {camp.total_sent?.toLocaleString() ?? 0} sent
                    </span>
                  </div>

                  <div className="flex gap-4">
                    <div className="text-center">
                      <span className="text-[10px] text-slate-500 uppercase tracking-wider block">Delivery</span>
                      <span className="text-xs font-bold text-slate-300">{camp.delivery_rate}%</span>
                    </div>
                    <div className="text-center">
                      <span className="text-[10px] text-slate-500 uppercase tracking-wider block">Open</span>
                      <span className="text-xs font-bold text-purple-400">{camp.open_rate}%</span>
                    </div>
                    <div className="text-center">
                      <span className="text-[10px] text-slate-500 uppercase tracking-wider block">Click</span>
                      <span className="text-xs font-bold text-teal-400">{camp.click_rate}%</span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Channel Comparison list */}
          <div className="p-6 rounded-xl border border-slate-800 bg-slate-900/20 space-y-6">
            <h3 className="font-bold text-white text-lg flex items-center gap-2">
              <BarChart3 className="w-5 h-5 text-blue-400" />
              Channel Performance
            </h3>

            <div className="space-y-6">
              {channels.map((chan) => (
                <div key={chan.channel} className="space-y-2">
                  <div className="flex justify-between items-center text-xs">
                    <span className="font-semibold text-slate-200 capitalize">
                      {chan.channel}
                    </span>
                    <span className="text-slate-400">
                      CTR: {chan.click_rate}%
                    </span>
                  </div>
                  <div className="w-full bg-slate-950 h-2.5 rounded-full overflow-hidden border border-slate-800">
                    <div
                      className="bg-purple-500 h-full rounded-full"
                      style={{ width: `${chan.click_rate * 2.5}%` }} // Scale to make chart visible
                    />
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
