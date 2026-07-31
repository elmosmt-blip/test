# Mirtec: Automotive PCBA Quality Control and 3D AOI Inspection Innovations

*Published YYYY-MM-DD · Editorial Section: SMT Equipment Review · Source: SMT Today Magazine Issue 80*

Automotive electronics assembly lines operate under some of the most stringent quality mandates in industrial manufacturing. With electronic control units (ECUs), ADAS sensor clusters, and EV power inverters subjected to extreme thermal shock and mechanical vibration, zero-defect quality control requires inspection systems that detect micro-defects at full inline line speed. In Issue 80 of *SMT Today Magazine*, Mirtec highlights its latest **MV-6 OMNI** 3D Automated Optical Inspection (AOI) series, demonstrating how 15-megapixel CoaXPress camera architecture and multi-frequency digital moiré projectors achieve rapid inspection speeds without increasing false-call rates.

For SMT quality engineers and automotive PCBA auditors, evaluating inline 3D AOI systems requires looking closely at camera field-of-view (FOV) efficiency, 3D fringe projection frequencies, and false-call mitigation on tall connectors and reflective solder joints.

---

## 1. 15MP CoaXPress Camera Architecture and High-Speed FOV

A persistent engineering trade-off in inline 3D AOI is the conflict between optical pixel resolution and PCB scanning speed. Utilizing high-magnification lenses to inspect 01005 chips shrinks the field of view (FOV), requiring more mechanical X-Y gantry steps to scan a board and creating a production bottleneck after the reflow oven.

In the technical feature in *SMT Today*, Mirtec addresses this bottleneck with its **15-Megapixel CoaXPress Vision System**. By combining a high-resolution 15MP top-down camera with a proprietary CoaXPress data interface, the system captures large FOV images at **120 frames per second**, achieving a sustained inline 3D inspection speed of **120 cm²/sec** at a native optical resolution of **10 µm/pixel** (with an optional **7.7 µm/pixel** lens for ultra-fine-pitch microelectronics).

```
       [ 15MP Top-Down Camera ] ──(120 fps CoaXPress Link)──> [ GPU Processing ]
                  │
        (Large 10 µm FOV)
                  ▼
       [ 120 cm²/s Inline Speed ] ──> [ Zero-Downtime Automotive Reflow Line ]
```

### Optical & Scanning Performance Metrics
- **Inline Scanning Speed:** Sustained 3D inspection speed of **120 cm²/s**, allowing 100% 3D inspection of a typical 250 x 200 mm automotive ECU board in under **5 seconds**.
- **Four-Angle Side-View Cameras:** Four independent 18-megapixel angled side-view cameras inspect solder fillets under J-lead components, PLCCs, and gull-wing QFPs where top-down projection is shadowed by the component body.
- **Defect Detection Accuracy:** Proven to achieve an inline defect detection rate of **99.8%** while capping the false-call rate below **50 ppm** on complex automotive assemblies.

---

## 2. Multi-Frequency Digital Moiré Projection and Tall Component Inspection

A major limitation of single-frequency moiré projection in 3D AOI is height ambiguity when inspecting tall electrolytic capacitors, automotive connectors, and relays alongside low-profile 0402 resistors. When a projection pattern wraps around a component exceeding **5 mm** in height, phase discontinuity creates 3D reconstruction errors.

Mirtec’s MV-6 OMNI architecture overcomes this through **Digital Multi-Frequency Quad Moiré Projection**. Four programmable digital projectors cast a sequence of fine, medium, and coarse moiré fringe frequencies across the PCB surface, allowing the vision processor to reconstruct accurate 3D profiles from **0 µm up to 25 mm** in height.

```
+--------------------------------------------------------------------------+
|               MULTI-FREQUENCY QUAD MOIRÉ PROJECTION ENGINE               |
+--------------------------------------------------------------------------+
|                                                                          |
|  [Coarse Fringe Pattern]  ──> [Resolve Tall Connectors (up to 25 mm)]    |
|  [Medium Fringe Pattern]  ──> [Inspect Standard IC Bodies & Leads]       |
|  [Fine Fringe Pattern]    ──> [Quantify 01005 Chip Fillet Volume]        |
|                                                                          |
+--------------------------------------------------------------------------+
```

### Key Measurement Capabilities
1. **25 mm Z-Axis Inspection Range:** Accurately measures the height and tilt of automotive connectors, press-fit pins, and electrolytic capacitors up to **25 mm** above the PCB surface without mechanical collision or defocusing.
2. **Coplanarity & Pin-Height Verification:** For multi-pin connectors, the 3D AOI system quantifies individual pin heights to an accuracy of **±2 µm**, identifying bent or recessed pins before final mechanical casing assembly.

---

## 3. Automotive Quality Compliance and IPC-CFX Line Integration

In automotive electronics manufacturing, compliance with ISO/TS 16949 and VDA 6.3 standards mandates complete traceability of every inspected solder joint. Mirtec integrates deep learning defect classification with **IPC-CFX** and **IPC-HERMES-9852** protocols to automate factory floor traceability.

### Smart Factory & Traceability Features
- **Deep Learning False-Call Suppression:** Mirtec's AI engine learns line-specific acceptable variations in PCB silkscreen shifting, solder mask color differences, and lead oxidation, reducing operator review load by up to **70%**.
- **Real-Time Closed-Loop MES Integration:** Solder fillet volume and pin coplanarity measurements are streamed via IPC-CFX to line-level MES databases, associating every 3D inspection record with the PCB's unique laser-etched 2D barcode.
- **Automated Root-Cause Analysis:** When systematic solder defects occur on a specific component footprint, Mirtec's Intellisys software correlates AOI failure coordinates with upstream SPI solder paste volume logs to isolate whether the root cause was stencil clogging, pick-and-place nozzle drift, or reflow profiling.

---

## 4. Engineering Takeaways & Production Line Recommendations

For SMT engineering teams operating automotive PCBA lines or evaluating Mirtec 3D AOI systems, the technical data in Issue 80 of *SMT Today* suggests concrete quality control guidelines:

1. **Standardize on 10 µm Optical Resolution for Automotive Lines:**
   - While 7.7 µm resolution is available, **10 µm/pixel** resolution provides the optimal balance of **120 cm²/s** inline scanning speed and sufficient pixel density to reliably inspect 0201 and 0402 passive components without creating reflow line bottlenecks.
2. **Implement Pin-Height Alarm Limits on Press-Fit Connectors:**
   - Configure explicit 3D Z-axis alarm thresholds (±50 µm tolerance) on all automotive header pins and press-fit connectors to prevent downstream mating failures in automotive wire-harness assembly.
3. **Audit False-Call Rates Weekly Using AI Classification Logs:**
   - Target a sustained line false-call rate below **50 ppm** by regularly retraining the AI classification engine on edge-case solder fillet reflections, eliminating operator fatigue at verification stations.

---

## 5. Sources

- **SMT Today Magazine — Issue 80 (DIGI):** Official digital edition featuring Mirtec MV-6 OMNI 3D AOI inspection systems, automotive PCBA quality control, and smart factory automation trends. URL: https://online.fliphtml5.com/kwnhb/fakj/
- **Mirtec Technical Specifications:** MV-6 OMNI CoaXPress 15MP camera resolution, 120 cm2/s inspection speed, and 25 mm 3D measurement range. URL: https://online.fliphtml5.com/kwnhb/fakj/
