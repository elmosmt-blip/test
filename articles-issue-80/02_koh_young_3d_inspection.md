# Koh Young Technology: True 3D SPI and AOI Metrology for Advanced Packaging

*Published YYYY-MM-DD · Editorial Section: SMT Equipment Review · Source: SMT Today Magazine Issue 80*

As electronics manufacturing converges with semiconductor advanced packaging—driven by AI accelerators, automotive ADAS modules, and heterogenous System-in-Package (SiP) assemblies—traditional 2D optical inspection can no longer guarantee zero-defect production. In Issue 80 of *SMT Today Magazine*, Koh Young Technology presents its latest True 3D Metrology platforms, detailing how phase-shift moiré profilometry and AI-powered false-call filtering solve the inspection challenges of ultra-miniature solder deposits, shiny micro-bumps, and high-density PCB assemblies.

For SMT quality managers and process engineers, the transition from 2D defect detection to 3D parametric metrology represents a shift from passive inspection to proactive closed-loop process control. This engineering review examines the optical principles, measurement repeatability, and production line impact of Koh Young’s latest SPI and AOI architectures.

---

## 1. Optical Principles: Overcoming Specular Reflections in 3D Profilometry

A primary failure mode of conventional inspection equipment when examining shiny solder deposits, WLP micro-bumps, or mirror-finished silicon dies is specular reflection. When coaxial or simple ring lighting illuminates a highly reflective solder joint, saturated pixels blind the sensor, creating height artifacts and false height-defect calls.

Koh Young’s True 3D architecture overcomes this limitation through **multi-projection phase-shift moiré optical profilometry**. By projecting sinusoidal light patterns from multiple angled projection projectors and capturing the distorted phase modulation via a telecentric camera array, the system reconstructs a complete 3D topological height map of every solder pad and component lead without specular blinding.

```
       [ Multi-Angle Moiré Projectors ]
             \        |        /
              ▼       ▼       ▼
      +-------------------------------+
      |  Shiny Solder Bump / Pad Top  |  ──> [ Telecentric Camera Array ]
      +-------------------------------+              │
                                                     ▼
                                           [ 3D Height Map Reconstructed ]
```

### Verified Performance Specifications
- **Z-Axis Height Resolution:** Sustained height measurement resolution of **0.5 µm**, allowing precise solder volume quantification on 008004 (0201 metric) pads and micro-bump arrays down to **25 µm** height.
- **Parametric Repeatability (Gage R&R):** Less than **10% Gage R&R** under continuous production speeds, ensuring that height and volume measurements remain stable across shifts and ambient temperature fluctuations.
- **First Pass Yield (FPY) Impact:** Proven to maintain an inline defect detection accuracy of **99.8%** while achieving a First Pass Yield exceeding **99.5%** on dense automotive electronics lines.

---

## 2. AI-Driven False-Call Reduction and Warpage Compensation

In high-density PCBA production, PCB substrate warpage induced by reflow thermal cycles is a major contributor to false calls in Automated Optical Inspection (AOI). If a board warps by even **100 µm**, static 2D focus planes and rigid height thresholds will misflag good solder joints as lifted leads or insufficient solder.

In the technical feature in *SMT Today*, Koh Young details its **AI-powered Dynamic Fiducial and Warpage Compensation (DFWC)** engine.

```
+--------------------------------------------------------------------------+
|                  AI-POWERED WARPAGE COMPENSATION LOOP                    |
+--------------------------------------------------------------------------+
|                                                                          |
|  [3D Board Surface Scan] ──> [Fit Real-Time B-Spline Reference Plane]   |
|                                         │                                |
|                                         ▼                                |
|  [Inspect Component Leads] <── [Calculate Local Pad Z-Zero Datum]        |
|                                                                          |
+--------------------------------------------------------------------------+
```

### Key AI Processing Features
1. **Local Z-Zero Tracking:** Instead of relying on a global PCB surface datum, the 3D AOI system calculates a local Z-zero reference plane for every individual component footprint by measuring bare board regions immediately adjacent to the copper pads. This eliminates false lifted-lead defects on bowed substrates.
2. **Synthetic AI Defect Training:** Koh Young's inspection engine utilizes generative AI models trained on millions of true and false defect pairs. This allows the system to distinguish between harmless flux residue reflections and genuine cold solder joints or micro-bridging without requiring operators to relax inspection thresholds.

---

## 3. Closed-Loop KSMART Process Control & Line Integration

The greatest return on investment for True 3D SPI and AOI systems lies in using parametric measurement data to correct upstream process machines before defects occur. Koh Young’s KSMART software suite implements real-time closed-loop feedback via standard **IPC-CFX** telemetry.

```
  [ Koh Young 3D SPI ] ──(Solder Paste Volume Drift)──> [ SMT Screen Printer ]
          │                                                    │
          │                                             (Auto Clean/Offset)
          ▼                                                    ▼
  [ Koh Young 3D AOI ] ──(Component XY Placement Offset)─> [ Fuji / Pick-n-Place ]
```

### Closed-Loop Integration Capabilities
- **Printer Solder Paste Offset Correction:** When 3D SPI detects systematic solder paste volume or alignment drift across a PCB panel, it transmits a correction vector directly to the SMT stencil printer. The printer adjusts its X-Y stencil alignment or initiates an automated under-stencil dry/wet wipe before the next PCB enters the machine.
- **Mounter Placement Offset Compensation:** When 3D AOI identifies that a specific component package consistently drifts by more than **20 µm** during reflow due to uneven pad paste volumes, it feeds back placement correction coordinates to the pick-and-place module to offset initial component placement.

---

## 4. Engineering Takeaways & Production Line Recommendations

For SMT engineering teams deploying Koh Young True 3D SPI or AOI platforms, the technical specifications published in Issue 80 of *SMT Today* point to concrete operational best practices:

1. **Establish 100% Solder Paste Volume Control on BGA/QFN Pads:**
   - Configure 3D SPI alarm limits on solder paste volume rather than area or height alone. A minimum solder volume threshold of **80%** on QFN thermal pads and BGA pads is critical to prevent voiding and open balls.
2. **Leverage IPC-CFX for Multi-Vendor Line Interoperability:**
   - Ensure that SPI and AOI inspection systems are connected to placement machines and screen printers via native IPC-CFX rather than proprietary vendor drivers. This ensures sub-second transmission of inspection parametric data across mixed-vendor SMT lines.
3. **Audit Gage R&R on Micro-Bump Packages:**
   - When deploying 3D AOI for semiconductor advanced packaging, perform monthly Gage R&R verifications using a certified height calibration target to confirm that **0.5 µm** Z-axis repeatability is maintained across cleaning cycles.

---

## 5. Sources

- **SMT Today Magazine — Issue 80 (DIGI):** Official digital edition featuring Koh Young Technology 3D SPI and AOI metrology, advanced packaging inspection, and KSMART process integration. URL: https://online.fliphtml5.com/kwnhb/fakj/
- **Koh Young Technology Technical Specifications:** True 3D profilometry resolution, repeatability, and Gage R&R engineering data. URL: https://online.fliphtml5.com/kwnhb/fakj/
