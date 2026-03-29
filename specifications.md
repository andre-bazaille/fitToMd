# **Technical Specification: FIT to Markdown Extractor**

## **1\. Project Overview**

A CLI tool designed to parse Garmin/FIT binary files and generate a structured, token-efficient Markdown report. This report serves as the "raw data" input for LLM-based coaching analysis, replacing the need to upload large binary files.

## **2\. Core Dependencies**

* **Language:** Python 3.9+  
* **Library:** fitdecode (chosen for modern support and performance).  
* **Utility:** tabulate (optional, for clean MD table formatting).

## **3\. Data Extraction Logic**

The tool must iterate through record and lap messages to extract three specific layers of data:

### **A. Global Metadata (Header)**

Extracted from session and device\_info messages:

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

### **C. High-Resolution "Responsiveness" Samples**

To allow the LLM to judge "how fast HR goes up/down," the tool should extract raw samples at transition points (e.g., the first and last 60 seconds of a lap or workout step):

* **Sample Rate:** Every 5 or 10 seconds during transitions.  
* **Data Points:** \[Timestamp, Heart Rate, Speed, Grade\]. Grade should use the native FIT field when available, otherwise estimate it from the same smoothed altitude profile used for elevation gain/loss.

## **4\. Output Format (Markdown)**

The output should follow this exact structure to ensure LLM readability:  
\# FIT Report: \[YYYY-MM-DD\] \[Activity Name\]

\#\# Session Summary  
\- \*\*Total Distance:\*\* 10.50 km  
\- \*\*Total Time:\*\* 55:12  
\- \*\*Elevation Gain/Loss:\*\* \+120m / \-115m  
\- \*\*Avg/Max HR:\*\* 142 / 175 bpm  
\- \*\*Avg Cadence:\*\* 172 spm

\#\# Kilometric Splits  
| Km | Time | Pace | Elev \+/- | Avg HR | Max HR | Avg Cad |  
|---|---|---|---|---|---|---|  
| 1 | 5:30 | 5:30 | \+5m | 125 | 135 | 168 |  
| 2 | 5:15 | 5:15 | \-2m | 138 | 142 | 172 |

\#\# Heart Rate Dynamics (Recovery & Ramp)  
\- \*\*Transition: Stop of Lap 4 to Start of Lap 5\*\*  
  \- T+0s: 175 bpm (Pace: 4:00/km, Grade: -2.40%)  
  \- T+10s: 165 bpm (Pace: -)  
  \- T+30s: 150 bpm (Pace: -)  
  \- T+60s: 135 bpm (Pace: -)

## **5\. Technical Challenges & Solutions**

* **Unit Conversion:** Convert semicircles to degrees (if GPS is needed) and meters/sec to min/km.  
* **Message Filtering:** Only process record messages containing heart\_rate and distance to avoid bloating the output.  
* **Missing Data:** Implement null checks for sensors and optional fields (e.g., if no Power Meter or Cadence sensor was present). If a native Grade field is missing, derive a stable estimate from smoothed altitude and omit it only when movement is paused or there is not enough distance context.

## **6\. Token Optimization Strategy**

* **Sampling:** Instead of 1-second data for a 2-hour run (7200 rows), use 1-km summaries and only high-res samples for recovery/intervals.  
* **Precision:** Round all floats to 2 decimal places.