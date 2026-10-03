// TuttiTrip — kontrakt propsów proponowanych komponentów (dokumentacja, nie kod).
import type { ReactNode } from "react";

export type Tone = "neutral" | "want" | "decline" | "warning" | "danger" | "solid" | "dashed";
export type Vote = "want" | "neutral" | "decline";
export type DeclineReason = "too_expensive" | "too_far" | "not_my_vibe" | "too_crowded" | "too_hard_for_child" | "other";
export type Verdict = "must" | "fits" | "iconic" | "skip";
export type RequirementState = "met" | "unmet" | "unknown";
export type Severity = "error" | "warning" | "hint";
export type Domain = "lodging" | "food" | "attractions" | "pace" | "cost";

export interface Person { id: string; name: string; initials: string; colorIndex: 1 | 2 | 3 | 4 | 5 | 6; isProfile?: boolean; role?: "host" | "cohost" | "member"; age?: number; }

export interface ButtonProps { variant?: "primary" | "ink" | "secondary" | "outline" | "ghost" | "destructive"; icon?: ReactNode; iconOnly?: boolean; cost?: string; busy?: boolean; disabled?: boolean; onClick?: () => void; children: ReactNode; }
export interface BadgeProps { tone?: Tone; icon?: ReactNode; children: ReactNode; }
export interface VerdictBadgeProps { verdict: Verdict; byHost?: boolean; }
export interface PersonChipProps { person: Person; size?: "sm" | "md"; showRole?: boolean; }
export interface RatingControlProps { value?: Vote; reason?: DeclineReason; subject?: Person; onChange: (vote: Vote, reason?: DeclineReason) => void; }
export interface ImportancePoolProps { values: Record<Domain, number>; total?: 10; person?: Person; onChange: (values: Record<Domain, number>) => void; }
export interface InterviewCardProps { messages: { from: "assistant" | "user"; text: string }[]; question: { kind: "slider" | "toggles" | "swipe"; label: string; min?: number; max?: number; unit?: string; options?: string[] }; known: { label: string; value: string }[]; onAnswer: (value: unknown) => void; onSkip: () => void; onBuildNow: () => void; }
export interface PlaceCardProps { name: string; kind: string; distanceKm: number; openHours?: string; verdict: Verdict; votes: { person: Person; vote: Vote; reason?: DeclineReason }[]; rationale: string; priceTotal?: number; fareName?: string; verified: boolean; sourceUrl?: string; onOpen?: () => void; }
export interface FairnessMeterProps { score: number; floor: number; people: { person: Person; satisfaction: number }[]; }
export interface FairnessLedgerProps { rows: { person: Person; fromOwnList: number; byDomain: Record<Domain, number> }[]; note?: string; }
export interface PlanTimelineProps { title: string; items: ({ kind: "place"; time: string; name: string; meta?: string; iconic?: boolean } | { kind: "leg"; mode: "walk" | "bus" | "tram" | "car"; minutes: number; price?: number } | { kind: "break"; time: string; label: string })[]; }
export interface LinterReportProps { pastedCount: number; ourCount: number; issues: { severity: Severity; title: string; detail?: string; ruleId: string; ruleLabel: string; day?: number; time?: string; fix?: { label: string; onFix: () => void } }[]; }
export interface PlanProgressProps { steps: { label: string; state: "done" | "active" | "pending" }[]; peopleCount: number; }
export interface RequirementCheckProps { offerName: string; nights: string; items: { requirement: string; state: RequirementState; quote?: string }[]; }
export interface OverrideCostProps { action: "force" | "block" | "go_anyway"; placeName: string; against: Person[]; fairness: [number, number]; budgetDelta: number; minutesDelta: number; onConfirm: () => void; onCancel: () => void; }
export interface ApprovalCardProps { title: string; amount?: string; reason: string; affects: string; onApprove: () => void; onReject: () => void; }
export interface BudgetBarProps { label: string; from: number; to: number; marginPct: number; spent: number; currency?: "PLN" | string; }
export interface ReplanBarProps { isHost: boolean; onReplan: (reason: "rain" | "tired" | "closed") => void; }
export interface SettlementProps { expenses: { id: string; title: string; amount: number; currency: string; paidBy: Person; excluded?: Person[]; at: string; needsConfirmation?: boolean }[]; transfers: { from: Person; to: Person; amount: number }[]; }
export interface BottomNavProps { active: "plan" | "places" | "assistant" | "expenses" | "group"; pendingApprovals?: number; }
