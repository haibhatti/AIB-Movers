# AIB Movers: Enterprise Transit & Fleet Management ERP

## 📌 Project Overview
Developed as my **first professional internship project**, AIB Movers is a comprehensive, modular monolith Enterprise Resource Planning (ERP) system designed for inter-city bus transit companies. Built with Django, the system handles public-facing ticket reservations, dynamic segment-based pricing, fleet logistics, HR performance tracking, and live passenger support.

## 🚀 Core Engineering Features

* **Algorithmic Dynamic Pricing:** Utilizes sequenced `RouteNode` models with cumulative distances and `DurationFields` to mathematically interpolate intermediate arrival times and exact segment fares.
* **Concurrency & Race Condition Prevention:** Ticket checkouts are wrapped in `transaction.atomic()` blocks using `select_for_update()`, making duplicate seat purchases mathematically impossible.
* **Strict Role-Based Access Control (RBAC):** Decoupled logic across 25+ role-isolated templates for Admins, Booking Agents, Support Agents, Drivers, and Conductors.
* **Immutable Auditing:** Every sensitive staff action is permanently recorded in an un-editable `AuditLog` to ensure total employee accountability.
* **Automated Database Signals:** Django `@receiver` signals automatically monitor and dynamically recalculate Master Origin and Destination endpoints in the background.

## 🛠 Technical Stack
* **Backend:** Django (Python), Relational SQL Mapping
* **Frontend:** Pure CSS architecture (No Bootstrap), Vanilla JS
* **Analytics:** Google Charts API via secure `json_script` templating
* **Task Management:** Python `threading` for non-blocking email dispatches
