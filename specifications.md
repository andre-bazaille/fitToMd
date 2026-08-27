# **Technical Specification: FIT to Markdown Extractor**

## **1\. Project Overview**

A CLI tool designed to parse Garmin/FIT binary files and generate a structured, token-efficient Markdown report. This report serves as the "raw data" input for LLM-based coaching analysis, replacing the need to upload large binary files.

## **2\. Core Dependencies**

* **Language:** Python 3.9+  
* **Library:** fitdecode (chosen for modern support and performance).

## **3\. Data Extraction Logic**

The tool must iterate through record and lap messages to extract three specific layers of data:

### **A. Global Metadata (Header)**

Extracted from the session message and normalized before entering the domain:

* **Timestamp:** Start time (Local/UTC).  
* **Activity Type:** Running, Trail Running, etc.  
* **Total Distance:** In kilometers (converted from meters).  
* **Total Duration:** Timer time vs. Elapsed time.  
* **Total Elevation:** Ascent and Descent (meters).  
* **Averages:** Heart Rate, Cadence, Speed.

### **B. Segmented Data (Splits Table)**

Calculated based on 1.0 km increments (or using native lap messages if preferred):

* **Lap Number:** 1, 2, 3...  
* **Pace:** Format MM:SS per km.  
* **Elevation Delta:** Net gain/loss for that specific kilometer.  
* **HR Profile:** Average and Maximum Heart Rate for that kilometer.  
* **Cadence:** Average steps per minute.

### **C. Per-Kilometer Dynamics Samples**

To allow the LLM to assess heart-rate, pace, and grade evolution, the tool should extract samples throughout every completed kilometer:

* **Sample Rate:** Configurable interval, defaulting to every 30 seconds.
* **Data Points:** \[Elapsed Time, Heart Rate, Speed, Grade\]. Grade should use the native FIT field when available, otherwise estimate it from the same smoothed altitude profile used for elevation gain/loss.

## **4\. Output Format (Markdown)**

The output follows this structure to ensure LLM readability:

```markdown
# FIT Report: 2026-03-29 Running

## Session Summary
- **Start Time:** 2026-03-29 06:30:00
- **Activity Type:** Running
- **Total Distance:** 10.50 km
- **Total Time:** 55:12
- **Elapsed Time:** 55:40
- **Elevation Gain/Loss:** +120m / -115m
- **Avg/Max HR:** 142 / 175 bpm
- **Avg Cadence:** 172 spm
- **Avg Pace:** 5:15/km
- **Weather:** FIT and historical weather data unavailable

## Kilometric Splits
| Km | Time | Pace | Elev +/- | Avg HR | Max HR | Avg Cad |
|---|---|---|---|---|---|---|
| 1 | 5:30 | 5:30 | +5m | 125 | 135 | 168 |
| 2 | 5:15 | 5:15 | -2m | 138 | 142 | 172 |

## Heart Rate Dynamics (Per Kilometer)
- **Km 4**
  - 0:00: 145 bpm (Pace: 5:00/km, Grade: -2.40%)
  - 0:30: 150 bpm (Pace: 4:55/km, Grade: -1.20%)
  - 1:00: 155 bpm (Pace: 4:50/km, Grade: 0.50%)
```

## **5\. Technical Challenges & Solutions**

* **Unit Conversion:** Convert semicircles to degrees (if GPS is needed) and meters/sec to min/km.  
* **Message Filtering:** Ignore records without a timestamp and records that contain none of the supported activity metrics.
* **Missing Data:** Implement null checks for sensors and optional fields (e.g., if no Power Meter or Cadence sensor was present). If a native Grade field is missing, derive a stable estimate from smoothed altitude and omit it only when movement is paused or there is not enough distance context.

## **6\. Token Optimization Strategy**

* **Sampling:** Instead of 1-second data for a 2-hour run (7200 rows), use 1-km summaries and configurable samples within each completed kilometer.
* **Precision:** Round all floats to 2 decimal places.

## **7\. Architecture and External Enrichment**

FIT decoding produces decoder-independent activity entities before reporting rules are applied. Domain code must not import application or infrastructure modules; this dependency rule is enforced by an architecture test.

Weather and DEM elevation enrichment are disabled by default. When explicitly enabled, Open-Meteo receives the activity start location and time, while OpenTopoData receives sampled route coordinates. DEM-corrected records are used consistently for session elevation, split elevation, and derived grades.
