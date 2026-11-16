import SwiftUI
import MapKit

@main
struct PinpointApp: App {
    var body: some Scene { WindowGroup { ResponderView() } }
}

private enum Palette {
    static let red = Color(red: 0.78, green: 0.145, blue: 0.21)
    static let route = Color(uiColor: UIColor { traits in
        traits.userInterfaceStyle == .dark
            ? UIColor(red: 0.38, green: 0.69, blue: 0.90, alpha: 1)
            : UIColor(red: 0.16, green: 0.36, blue: 0.52, alpha: 1)
    })
}

struct ResponderView: View {
    @State private var stage = 0
    @StateObject private var guide = IndoorGuide()
    @State private var packet = false
    @State private var satellite = false
    @State private var camera: MapCameraPosition = .region(Self.region)
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    private static let region = MKCoordinateRegion(center: CLLocationCoordinate2D(latitude: 29.9631, longitude: -95.5354), span: MKCoordinateSpan(latitudeDelta: 0.003, longitudeDelta: 0.004))
    private let approach = [CLLocationCoordinate2D(latitude: 29.9622, longitude: -95.5370), CLLocationCoordinate2D(latitude: 29.9628, longitude: -95.5360), CLLocationCoordinate2D(latitude: 29.9631196, longitude: -95.5355522), CLLocationCoordinate2D(latitude: 29.963065384499192, longitude: -95.53539548150664)]

    private var indoors: Bool { stage >= 3 }
    private func advance() {
        withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.3)) { stage += 1 }
    }
    var body: some View {
        GeometryReader { geometry in
            ZStack {
                if indoors {
                    ZStack {
                        IndoorViewer(guide: guide)
                        if let message = guide.error {
                            VStack(spacing: 12) {
                                Image(systemName: "wifi.exclamationmark").font(.title)
                                Text(message).font(.body).multilineTextAlignment(.center)
                                Button("Retry scan") { guide.retry() }.buttonStyle(.borderedProminent)
                            }.padding(24).background(.regularMaterial, in: RoundedRectangle(cornerRadius: 16)).padding(20)
                        } else if !guide.ready {
                            VStack(spacing: 12) {
                                ProgressView()
                                Text("Opening building scan").font(.headline)
                                Text("Loading the 3D model and red route arrows").font(.subheadline).foregroundStyle(.secondary)
                            }.padding(24).background(.regularMaterial, in: RoundedRectangle(cornerRadius: 16)).padding(20)
                        }
                    }
                }
                else {
                    Map(position: $camera) {
                        MapPolyline(coordinates: approach).stroke(.white, lineWidth: 11)
                        MapPolyline(coordinates: approach).stroke(Palette.route, lineWidth: 6)
                        Annotation("Reception entrance", coordinate: approach.last!, anchor: .bottom) {
                            VStack(spacing: 4) {
                                if !dynamicTypeSize.isAccessibilitySize {
                                    Text("WEST RECEPTION").font(.caption2.bold()).padding(8).background(.background, in: RoundedRectangle(cornerRadius: 8))
                                }
                                Image(systemName: "mappin.circle.fill").font(.largeTitle).foregroundStyle(Palette.red, .white)
                            }.accessibilityElement(children: .ignore).accessibilityLabel("West reception entrance")
                        }
                        Annotation("Medic 4 · simulated", coordinate: approach[stage == 0 ? 0 : stage == 1 ? 2 : 3]) {
                            Image(systemName: "location.north.fill").font(.title3.bold()).foregroundStyle(.white).padding(13).background(Palette.route, in: Circle()).overlay(Circle().stroke(.white, lineWidth: 4))
                                .accessibilityLabel("Medic 4, simulated position")
                        }
                    }
                    .mapStyle(satellite ? .imagery(elevation: .flat) : .standard(elevation: .flat, pointsOfInterest: .excludingAll, showsTraffic: false))
                    .mapControls { MapCompass(); MapScaleView() }
                    .ignoresSafeArea()
                }
            }
            .safeAreaInset(edge: .top, spacing: 12) {
                VStack(spacing: 12) {
                    HStack {
                        if dynamicTypeSize.isAccessibilitySize {
                            Text("Pinpoint").font(.caption.bold())
                        } else {
                            Label("Pinpoint", systemImage: "mappin.and.ellipse").font(.headline)
                        }
                        Spacer()
                        Menu {
                            Button("Restart demo", systemImage: "arrow.counterclockwise") { stage = 0; camera = .region(Self.region); guide.reset() }
                            if !indoors { Button(satellite ? "Street map" : "Satellite map", systemImage: "map") { satellite.toggle() } }
                        } label: { Image(systemName: "ellipsis.circle").font(.title3).frame(width: 44, height: 44) }
                        .accessibilityLabel("Demo options")
                    }.padding(.horizontal, 16).background(.regularMaterial)
                    if indoors {
                        Picker("Building view", selection: Binding(get: { guide.mode }, set: { guide.command($0, reduceMotion: reduceMotion) })) {
                            Text("Dollhouse").tag("dollhouse")
                            Text("First person").tag("inside")
                        }.pickerStyle(.segmented).disabled(!guide.ready || guide.busy).padding(.horizontal, 16)
                    } else if stage > 0 && !dynamicTypeSize.isAccessibilitySize {
                        HStack(spacing: 14) {
                            Image(systemName: "arrow.up").font(.title.bold()).foregroundStyle(Palette.red)
                            VStack(alignment: .leading, spacing: 4) {
                                Text(stage == 1 ? "Approach Willow Centre Dr" : "Approach west reception").font(.headline)
                                Text(stage == 1 ? "Follow the highlighted demo route" : "Demo staging · west parking lot").font(.subheadline).foregroundStyle(.secondary)
                            }
                            Spacer(minLength: 0)
                        }.padding(16).background(.regularMaterial, in: RoundedRectangle(cornerRadius: 16)).padding(.horizontal, 16)
                    }
                }
            }
            .safeAreaInset(edge: .bottom, spacing: 12) {
                if indoors {
                    IndoorRoutePanel(guide: guide, completed: stage == 5, maximumHeight: dynamicTypeSize.isAccessibilitySize ? geometry.size.height * 0.58 : nil, showBrief: { packet = true }, restart: {
                        stage = 0
                        camera = .region(Self.region)
                        guide.reset()
                    }, finish: { stage = 5 })
                    .padding(.horizontal, 16).padding(.bottom, 8)
                } else {
                    OutdoorIncidentPanel(stage: stage,
                        maximumHeight: dynamicTypeSize.isAccessibilitySize ? geometry.size.height * 0.58 : nil,
                        showBrief: { packet = true }, recenter: { camera = .region(Self.region) }, advance: advance)
                        .padding(.horizontal, 16).padding(.bottom, 8)
                }
            }
            .tint(Palette.red)
            #if DEBUG
            .onAppear {
                if let index = ProcessInfo.processInfo.arguments.firstIndex(of: "-demo-stage"),
                   ProcessInfo.processInfo.arguments.indices.contains(index + 1),
                   let value = Int(ProcessInfo.processInfo.arguments[index + 1]) { stage = min(5, max(0, value)) }
                if ProcessInfo.processInfo.arguments.contains("-demo-brief") { packet = true }
            }
            #endif
            .sheet(isPresented: $packet) {
                NavigationStack {
                    List {
                        Section("Incident PP-1042") {
                            LabeledContent("Response", value: "Medical assist")
                            LabeledContent("Unit", value: "Medic 4")
                            Text("12814 Willow Centre Dr, Houston\nFloor 1 · Conference room")
                        }
                        Section("Building approach") {
                            Text("The sample approach ends at west reception. Exterior anchors are illustrative and must be verified on site.")
                        }
                        Section("Caller location") {
                            Text("The sample caller destination is the conference room on floor 1.")
                            Text("Use Dollhouse to see the route, then First person to follow the red arrows. Next waypoint moves between captured scan positions.")
                        }
                        Section("About this demo") {
                            Text("The building is a real Matterport scan. The incident and selected route are a demonstration, not validated emergency directions. No location is collected and no dispatch status is sent. Internet is required for Apple Maps and Matterport imagery; no dispatcher server is needed.")
                        }
                    }.navigationTitle("Incident brief").navigationBarTitleDisplayMode(.inline)
                        .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { packet = false } } }
                }
            }
        }
    }
}


/// Keeps destination, progress, and waypoint actions together beside the scan.
private struct IndoorRoutePanel: View {
    @ObservedObject var guide: IndoorGuide
    let completed: Bool
    let maximumHeight: CGFloat?
    let showBrief: () -> Void
    let restart: () -> Void
    let finish: () -> Void
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    private var status: String {
        if completed { return "Demo complete · no status transmitted" }
        if guide.error != nil { return "Scan unavailable · retry above" }
        if !guide.ready { return "Opening the building scan…" }
        if guide.busy { return "Moving to the selected view…" }
        if !guide.onRoute { return "Exploring off route · return to continue" }
        if guide.atDestination { return "Destination reached · confirm below" }
        return guide.mode == "dollhouse" ? "Red arrows show the route from reception" : "Follow the red arrows"
    }
    private var action: String {
        if completed { return "Run demo again" }
        if guide.error != nil { return "Scan unavailable" }
        if !guide.ready { return "Loading scan…" }
        if guide.busy { return "Moving…" }
        if !guide.onRoute { return "Return to route" }
        if guide.atDestination { return "Mark caller reached" }
        return guide.mode == "dollhouse" ? "Start walkthrough" : "Next waypoint"
    }
    private var compactAction: String {
        if completed { return "Restart" }
        if guide.error != nil { return "Unavailable" }
        if !guide.ready { return "Loading…" }
        if guide.busy { return "Moving…" }
        if !guide.onRoute { return "Return" }
        if guide.atDestination { return "Confirm arrival" }
        return guide.mode == "dollhouse" ? "Start" : "Next"
    }
    private var unavailable: Bool { !guide.ready || guide.busy || guide.error != nil }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if dynamicTypeSize.isAccessibilitySize {
                ScrollView {
                    IndoorRouteDetails(guide: guide, completed: completed, status: status, showBrief: showBrief)
                }.scrollIndicators(.visible)
            } else {
                IndoorRouteDetails(guide: guide, completed: completed, status: status, showBrief: showBrief)
            }
            HStack(spacing: 12) {
                if !completed {
                    Button { guide.command("previous", reduceMotion: reduceMotion) } label: {
                        Image(systemName: "arrow.backward").font(.headline).frame(width: 44, height: 44)
                    }.buttonStyle(.bordered)
                        .disabled(unavailable || !guide.onRoute || guide.waypoint == 0)
                        .accessibilityLabel("Previous waypoint")
                        .accessibilityHint("Moves back one captured scan position")
                }
                Button {
                    if completed { restart() }
                    else if guide.atDestination { finish() }
                    else { guide.command((guide.mode == "dollhouse" || !guide.onRoute) ? "inside" : "next", reduceMotion: reduceMotion) }
                } label: {
                    HStack(spacing: 8) {
                        if guide.busy && !completed { ProgressView().tint(.white).accessibilityHidden(true) }
                        Text(dynamicTypeSize.isAccessibilitySize ? compactAction : action)
                            .multilineTextAlignment(.center).fixedSize(horizontal: false, vertical: true)
                        if !guide.busy && !dynamicTypeSize.isAccessibilitySize {
                            Image(systemName: completed ? "arrow.counterclockwise" : "arrow.right").accessibilityHidden(true)
                        }
                    }.font(.headline).frame(maxWidth: .infinity, minHeight: 44)
                }.buttonStyle(.borderedProminent).tint(Palette.red)
                    .disabled(!completed && unavailable).accessibilityLabel(action).accessibilityIdentifier("advanceDemo")
            }
        }.padding(16).frame(height: maximumHeight)
            .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 20))
    }
}


private struct IndoorRouteDetails: View {
    @ObservedObject var guide: IndoorGuide
    let completed: Bool
    let status: String
    let showBrief: () -> Void
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            let layout = dynamicTypeSize.isAccessibilitySize
                ? AnyLayout(VStackLayout(alignment: .leading, spacing: 8))
                : AnyLayout(HStackLayout(alignment: .top, spacing: 8))
            layout {
                VStack(alignment: .leading, spacing: 4) {
                    Text(completed ? "Caller reached" : "Conference room").font(.title3.bold())
                        .accessibilityAddTraits(.isHeader)
                    Text("Floor \(guide.floor) · Real scan, demo route").font(.caption).foregroundStyle(.secondary)
                }.fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .accessibilityElement(children: .combine)
                Button(action: showBrief) {
                    Image(systemName: "doc.text").font(.title3).frame(minWidth: 44, minHeight: 44)
                }.accessibilityLabel("View incident brief")
            }
            if !completed && guide.ready {
                VStack(alignment: .leading, spacing: 6) {
                    Text(guide.onRoute ? "Waypoint \(guide.waypoint + 1) of \(guide.total)" : "Off route")
                        .font(.caption.weight(.medium)).monospacedDigit()
                        .fixedSize(horizontal: false, vertical: true)
                    ProgressView(value: Double(guide.waypoint), total: Double(max(1, guide.total - 1)))
                        .tint(guide.onRoute ? Palette.red : .secondary).accessibilityHidden(true)
                }.accessibilityElement(children: .combine)
            }
            Text(status).font(.subheadline).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}


private struct OutdoorIncidentPanel: View {
    let stage: Int
    let maximumHeight: CGFloat?
    let showBrief: () -> Void
    let recenter: () -> Void
    let advance: () -> Void
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    private var action: String { ["Accept & start demo", "Advance to approach", "Enter building"][min(stage, 2)] }

    var body: some View {
        VStack(spacing: 12) {
            HStack {
                if !dynamicTypeSize.isAccessibilitySize {
                    Label("Illustrative approach", systemImage: "point.topleft.down.to.point.bottomright.curvepath")
                        .font(.caption.weight(.semibold)).padding(10).background(.regularMaterial, in: Capsule())
                }
                Spacer()
                Button(action: recenter) {
                    Image(systemName: "location.viewfinder").font(.title3).frame(minWidth: 48, minHeight: 48).background(.regularMaterial, in: Circle())
                }.accessibilityLabel("Show entire route")
            }
            VStack(alignment: .leading, spacing: 12) {
                if dynamicTypeSize.isAccessibilitySize {
                    ScrollView { OutdoorIncidentDetails(stage: stage, showBrief: showBrief) }
                } else {
                    OutdoorIncidentDetails(stage: stage, showBrief: showBrief)
                }
                Button(action: advance) {
                    Text(dynamicTypeSize.isAccessibilitySize ? ["Accept", "Approach", "Enter building"][min(stage, 2)] : action)
                        .font(.headline).multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, minHeight: 44).padding(.vertical, 6)
                }.buttonStyle(.borderedProminent).tint(Palette.red)
                    .accessibilityLabel(action).accessibilityIdentifier("advanceDemo")
            }.padding(20).background(.regularMaterial, in: RoundedRectangle(cornerRadius: 20))
        }.frame(height: maximumHeight)
    }
}

private struct OutdoorIncidentDetails: View {
    let stage: Int
    let showBrief: () -> Void
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            let layout = dynamicTypeSize.isAccessibilitySize
                ? AnyLayout(VStackLayout(alignment: .leading, spacing: 8))
                : AnyLayout(HStackLayout(alignment: .top, spacing: 8))
            layout {
                VStack(alignment: .leading, spacing: 5) {
                    Text(stage == 0 ? "Medical assist" : "12814 Willow Centre Dr").font(.title3.bold())
                    Text(stage == 0 ? "12814 Willow Centre Dr · Houston" : "West reception · Houston")
                        .font(.subheadline).foregroundStyle(.secondary)
                }.fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading).accessibilityElement(children: .combine)
                Button(action: showBrief) {
                    Image(systemName: "doc.text").font(.title3).frame(minWidth: 44, minHeight: 44)
                }.accessibilityLabel("View incident brief")
            }
            Text("Medic 4 · PP-1042").font(.caption).foregroundStyle(.secondary)
            if dynamicTypeSize.isAccessibilitySize && stage > 0 {
                Text(stage == 1 ? "Follow the highlighted demo route" : "Demo staging · west parking lot").font(.subheadline)
            }
        }
    }
}
