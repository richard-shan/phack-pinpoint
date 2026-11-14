// Synthetic demo data only. No real property, patient, imagery, or provider calls.
export const STORAGE_KEY = "pinpoint.demo.incident.v1";

export function createIncident() {
  return {
    schemaVersion: 1,
    id: "PP-1042",
    revision: 1,
    mode: "demo",
    status: "review",
    stage: 5,
    awaitingCaller: false,
    startedAt: "14:32",
    elapsedSeconds: 38,
    type: "Medical assist",
    priority: "Priority 2",
    building: {
      id: "demo-hou-01",
      name: "Westbridge Medical Center",
      address: "2400 Demo Park Drive, Houston, TX",
      useType: "Medical office",
      floors: 2,
      area: "42,800 sq ft",
      tenant: "Westbridge Outpatient · Suite 204",
      scanDate: "Oct 24, 2026",
    },
    location: {
      floor: 2,
      room: "East elevator lobby",
      suite: "204",
      confidence: 0.94,
      nodeId: "L2-E07",
    },
    callerPhoto: "/assets/lobby.png",
    photoIsUpload: false,
    messages: [
      {
        id: "m1",
        role: "caller",
        text: "We’re on the second floor. My colleague needs help. I’m by the elevators.",
        time: "14:32",
      },
      {
        id: "m2",
        role: "caller",
        text: "This is what I can see.",
        image: true,
        time: "14:32",
      },
      {
        id: "m3",
        role: "agent",
        text: "Is there a blue 204 sign beside the elevator?",
        time: "14:32",
      },
      {
        id: "m4",
        role: "caller",
        text: "Yes, just to my right.",
        time: "14:33",
      },
    ],
    routes: {
      foot: {
        label: "Crew on foot",
        entrance: "East service entrance",
        door: "E-02",
        staging: "East loading bay",
        meters: 46,
        seconds: 64,
        savedSeconds: 42,
        vertical: "Stair B",
        steps: [
          "Stage at the east loading bay. Enter through door E-02.",
          "Continue 12 m to Stair B, past the extinguisher.",
          "Take Stair B to floor 2. Turn left into the east corridor.",
          "Continue 18 m to the elevator lobby, beside Suite 204.",
        ],
      },
      stretcher: {
        label: "Stretcher",
        entrance: "South accessible entrance",
        door: "S-01",
        staging: "South drop-off",
        meters: 72,
        seconds: 112,
        savedSeconds: 18,
        vertical: "Elevator A",
        steps: [
          "Stage at the south drop-off. Use accessible door S-01.",
          "Continue 24 m through reception to Elevator A.",
          "Take Elevator A to floor 2; turn right out of the elevator.",
          "Continue 10 m to the east elevator lobby, beside Suite 204.",
        ],
      },
    },
    evidence: [
      {
        id: "location",
        label: "Caller location",
        source: "Matterport",
        file: "location.json",
        confidence: "94%",
        summary:
          "Fixture node L2-E07 is the floor 2 east elevator lobby. Signage and elevator attributes support the demo match.",
        fields: {
          node_id: "L2-E07",
          floor: 2,
          suite: "204",
          score: 0.94,
          evidence_path: "/assets/lobby.png",
        },
        image: "/assets/lobby.png",
      },
      {
        id: "building",
        label: "Building & tenant",
        source: "CoStar",
        file: "brief.json",
        confidence: "Record",
        summary:
          "Synthetic property record for Westbridge Medical Center. Building and tenant details are placeholders for get_property().",
        fields: {
          record_id: "DEMO-HOU-01",
          use_type: "Medical office",
          stories: 2,
          tenant: "Westbridge Outpatient, Suite 204",
        },
      },
      {
        id: "site",
        label: "Staging & entrances",
        source: "Vexcel",
        file: "site.json",
        confidence: "88%",
        summary:
          "Illustrated site fixture: an east service entrance, a south accessible entrance, and two staging options. This is not a real aerial tile.",
        fields: {
          entrances: ["E-02", "S-01", "N-01"],
          staging: ["East loading bay", "South drop-off"],
          confidence: 0.88,
        },
      },
      {
        id: "route",
        label: "Routes & travel time",
        source: "Router",
        file: "route.json",
        confidence: "Fixture",
        summary:
          "Two precomputed fixture routes. Crew uses Stair B; the stretcher uses Elevator A. The console does not calculate routes.",
        fields: {
          foot: { door: "E-02", seconds: 64, meters: 46 },
          stretcher: { door: "S-01", seconds: 112, meters: 72 },
          baseline: "street-address door",
          time_costs: "Illustrative assumptions",
        },
      },
      {
        id: "equipment",
        label: "Equipment & access",
        source: "Matterport",
        file: "graph.json",
        confidence: "Observed",
        summary:
          "An extinguisher and AED are marked in the synthetic fixture. Current access, equipment availability, and elevator operation are unconfirmed.",
        fields: {
          equipment: ["AED · east elevator lobby", "Extinguisher · Stair B"],
          unconfirmed: [
            "Door E-02 unlocked",
            "Elevator operational",
            "Current equipment availability",
          ],
        },
      },
    ],
    packet: {
      notes:
        "Caller is waiting beside the elevator. Approach from the east for the crew on foot; use the south entrance for a stretcher.",
      unconfirmed: [
        "East service door is unlocked.",
        "Elevator is operational and available.",
        "Equipment is present and usable today.",
      ],
      audit: "5 fixture sources attached · 3 unconfirmed items",
    },
    decision: null,
  };
}

export const agentSteps = [
  {
    name: "Building identified",
    agent: "Records",
    detail: "Property and suite record attached",
    duration: "2.1s",
    evidence: "building",
  },
  {
    name: "Caller located",
    agent: "Locator",
    detail: "One question resolved two candidates",
    duration: "18.4s",
    evidence: "location",
  },
  {
    name: "Approach mapped",
    agent: "Site",
    detail: "3 entrances · 2 staging options",
    duration: "6.2s",
    evidence: "site",
  },
  {
    name: "Routes compared",
    agent: "Router",
    detail: "Foot and stretcher routes prepared",
    duration: "0.2s",
    evidence: "route",
  },
  {
    name: "Packet checked",
    agent: "Verifier",
    detail: "Sources attached; unknowns flagged",
    duration: "3.8s",
    evidence: "equipment",
  },
];
