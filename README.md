# Pollino Hazelnut Digital Twin 🌰🤖

> **Predictive irrigation decision-support system for climate-resilient hazelnut (*Corylus avellana*) cultivation in Piana di Cammarata (Castrovillari - CS, Calabria).**

[![Python 3.12](https://img.shields.io/badge/Python-3.12-blue.svg)](https://www.python.org/)
[![Checked with ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Verified with FAO-56](https://img.shields.io/badge/Agronomy-FAO--56%20Compliant-green.svg)](http://www.fao.org/3/x0490e/x0490e00.htm)

---

## 📋 Project Context & Purpose

The Piana di Cammarata represents a strategic area for high-quality hazelnut cultivation, particularly supported by local networks like the **Calabria In Guscio** enterprise network. However, historical climate stress (such as the severe 2025 summer drought analyzed in `Pollino_Hazelnut_Digital_Twin_Luigi_Ferrara_Challenge_Hazelnut_Agronomy.pdf`) highlights a critical vulnerability: **during the crucial nut-filling stage (June–July), hazelnuts face extreme hydric deficits and high heat stress.** 

Traditional reactive irrigation practices fail because shallow soil moisture (0–30 cm) drops rapidly days before visual symptoms present on the trees. When visual wilting occurs, loss of yield (void-nut rate, small kernel sizes) is already irreversible.

The **Pollino Hazelnut Digital Twin** solves this by shifting from a reactive approach to a **live predictive modeling pipeline**. By combining real-time local microclimate sensing, atmospheric vapor pressure calculations, and downscaled forecast data, it provides actionable traffic-light risk insights to agronomic managers before irreversible crop damage occurs.

### 🎯 Key Performance Targets
*   💧 **Environmental:** Reduce water consumption by **30–40%** compared to traditional fixed-schedule calendar irrigation.
*   📈 **Economic:** Stabilize commercial crop yield by **+15–20%**, specifically protecting nut weight and preventing empty shells.

---

## 🛠 Tech Stack

*   **Runtime & Package Manager:** Python 3.12 managed via `uv` (ultra-fast dependency sync).
*   **Ingestion Engine:** Playwright (headless automated browser scraping of the Netsense portal) & Open-Meteo API.
*   **Storage Layer:** PostgreSQL + SQLAlchemy ORM + Alembic migrations.
*   **Core Math/Testing:** `pytest` + `Hypothesis` (property-based invariant verification).
*   **Alerting Framework:** Multi-channel notification delivery (Telegram Bot API / SMTP).
*   **Next Milestone:** FastAPI-based analytical dashboard interface 🚀 *(Under Development)*.
