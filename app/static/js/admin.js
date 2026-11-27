"use strict";

(() => {
  const el = (id) => document.getElementById(id);
  const app = el("admin-app");
  if (!app) return;
  const state = {
    month: app.dataset.initialMonth, calendar: null, reservations: [], logs: [],
    page: 1, total: 0, horizon: null, horizonDirty: false,
    original: null, csrf: null, expires: 0, busy: false, ready: false, disposed: false,
  };
  const pendingRequests = new Set();
  const pageSize = 10;
  const datePattern = /^[0-9]{4}-[0-9]{2}-[0-9]{2}$/;
  const labels = { AVAILABLE: "가능", RESERVED: "예약", BLOCKED: "차단", PAST: "과거", OUTSIDE: "기간 밖" };
  const writeIds = ["save-reservation", "save-block", "save-settings"];

  function announce(message, kind = "info") {
    if (state.disposed) return;
    el("status").textContent = message;
    el("status").dataset.kind = kind;
  }

  function node(tag, text, className) {
    const result = document.createElement(tag);
    if (text !== undefined) result.textContent = text;
    if (className) result.className = className;
    return result;
  }

  function monthLength(month) {
    const [year, number] = month.split("-").map(Number);
    return [31, year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0) ? 29 : 28,
      31, 30, 31, 30, 31, 31, 30, 31, 30, 31][number - 1];
  }

  function firstWeekday(month) {
    const [year, number] = month.split("-").map(Number);
    const previous = year - 1;
    let days = previous * 365 + Math.floor(previous / 4) - Math.floor(previous / 100) + Math.floor(previous / 400);
    for (let m = 1; m < number; m += 1) days += monthLength(`${year}-${m}`);
    return (days + 1) % 7;
  }

  function shiftMonth(amount) {
    const [year, month] = state.month.split("-").map(Number);
    const index = year * 12 + month - 1 + amount;
    if (index < 12 || index >= 120000) return state.month;
    return `${String(Math.floor(index / 12)).padStart(4, "0")}-${String(index % 12 + 1).padStart(2, "0")}`;
  }

  function inputDates(id) {
    return el(id).value.trim().split(/[\s,]+/).filter(Boolean);
  }

  function validDates(id) {
    const dates = inputDates(id);
    if (!dates.length || new Set(dates).size !== dates.length || !dates.every((date) => {
      if (!datePattern.test(date)) return false;
      const [year, month, day] = date.split("-").map(Number);
      return year > 0 && month >= 1 && month <= 12 && day >= 1 && day <= monthLength(date.slice(0, 7));
    })) return null;
    return dates.sort();
  }

  function syncControls() {
    if (state.disposed) {
      for (const control of app.querySelectorAll("button, input, textarea")) control.disabled = true;
      return;
    }
    for (const control of app.querySelectorAll("button, input, textarea")) control.disabled = state.busy;
    for (const id of writeIds) el(id).disabled = state.busy || !state.ready || !state.csrf;
    for (const button of app.querySelectorAll("button[data-write]")) {
      button.disabled = state.busy || !state.ready || !state.csrf || button.dataset.unavailable === "true";
    }
    for (const button of el("calendar-grid").querySelectorAll("button")) {
      button.disabled = state.busy || !state.ready || button.dataset.unavailable === "true";
    }
    el("prev-month").disabled = state.busy || state.month === "0001-01";
    el("next-month").disabled = state.busy || state.month === "9999-12";
    el("logs-prev").disabled = state.busy || state.page <= 1;
    el("logs-next").disabled = state.busy || state.page * pageSize >= state.total;
    app.setAttribute("aria-busy", String(state.busy));
  }

  function renderDraft() {
    if (state.disposed) return;
    const original = state.original;
    el("form-heading").textContent = original ? "예약 수정" : "새 예약";
    el("save-reservation").textContent = original ? "수정 저장" : "예약 등록";
    el("edit-review").hidden = !original;
    if (!original) {
      el("edit-original").textContent = "";
      el("edit-diff").textContent = "";
      return;
    }
    el("edit-original").textContent = `기존 이름: ${original.name}\n기존 메모: ${original.memo || "없음"}\n기존 전체 날짜: ${original.dates.join(", ")}`;
    const dates = inputDates("dates");
    const added = dates.filter((date) => !original.dates.includes(date));
    const removed = original.dates.filter((date) => !dates.includes(date));
    el("edit-diff").textContent = `추가 날짜: ${added.join(", ") || "없음"}\n해제 날짜: ${removed.join(", ") || "없음"}\n`
      + `이름 변경: ${el("name").value.trim() === original.name ? "없음" : el("name").value.trim()}\n`
      + `메모 변경: ${el("memo").value === original.memo ? "없음" : el("memo").value || "삭제"}`;
  }

  function renderCalendar() {
    if (state.disposed) return;
    const [year, month] = state.month.split("-").map(Number);
    el("month-title").textContent = `${year}년 ${month}월`;
    el("month-title").dataset.month = state.month;
    const grid = el("calendar-grid");
    const focusDate = grid.contains(document.activeElement) ? document.activeElement.dataset.date : null;
    grid.replaceChildren();
    if (!state.calendar) return;
    const calendar = state.calendar;
    el("booking-window").textContent = `예약 가능: ${calendar.min_date} ~ ${calendar.max_date}`;
    const selected = new Set(inputDates("dates"));
    for (let i = 0; i < firstWeekday(state.month); i += 1) {
      const spacer = node("span");
      spacer.setAttribute("aria-hidden", "true");
      grid.append(spacer);
    }
    for (const day of calendar.dates) {
      const outside = day.date < calendar.min_date || day.date > calendar.max_date;
      const own = state.original?.dates.includes(day.date) && day.date >= calendar.today;
      const status = day.status === "AVAILABLE" && outside ? "OUTSIDE" : day.status;
      const button = node("button");
      button.type = "button";
      button.dataset.date = day.date;
      button.dataset.status = status;
      button.dataset.unavailable = String(!own && status !== "AVAILABLE");
      button.setAttribute("aria-pressed", String(selected.has(day.date)));
      button.setAttribute("aria-label", `${day.date} ${selected.has(day.date) ? "선택됨 · " : ""}${status === "AVAILABLE" ? "예약 가능" : labels[status]}`);
      if (day.date === calendar.today) button.setAttribute("aria-current", "date");
      button.append(node("span", String(Number(day.date.slice(-2)))), node("span", `${selected.has(day.date) ? "선택·" : ""}${labels[status]}`, "day-status"));
      button.addEventListener("click", () => {
        if (state.busy || !state.ready || button.disabled) return;
        selected.has(day.date) ? selected.delete(day.date) : selected.add(day.date);
        el("dates").value = [...selected].sort().join("\n");
        renderDraft();
        renderCalendar();
        syncControls();
      });
      grid.append(button);
    }
    if (focusDate) grid.querySelector(`[data-date="${focusDate}"]`)?.focus();
  }

  function startEdit(reservation) {
    if (state.busy) return;
    state.original = { ...reservation, dates: [...reservation.dates] };
    el("name").value = reservation.name;
    el("memo").value = reservation.memo;
    el("dates").value = reservation.dates.join("\n");
    renderDraft();
    renderCalendar();
    syncControls();
    el("name").focus();
  }

  function resetReservation() {
    state.original = null;
    el("reservation-form").reset();
    renderDraft();
    renderCalendar();
  }

  function renderLists() {
    if (state.disposed) return;
    const list = el("reservation-list");
    list.replaceChildren();
    for (const reservation of state.reservations) {
      const card = node("article", undefined, "card");
      card.dataset.id = reservation.id;
      card.append(node("h3", reservation.name), node("p", `상태: ${reservation.status === "CONFIRMED" ? "확정" : "취소됨"} (${reservation.status})`),
        node("p", `전체 날짜: ${reservation.dates.join(", ")}`), node("p", `메모: ${reservation.memo || "없음"}`));
      if (reservation.status === "CONFIRMED") {
        const row = node("div", undefined, "button-row");
        const edit = node("button", "수정");
        edit.type = "button";
        edit.addEventListener("click", () => startEdit(reservation));
        const cancel = node("button", "예약 취소");
        cancel.type = "button";
        cancel.dataset.write = "true";
        cancel.addEventListener("click", () => {
          if (!canWrite()) return;
          if (!window.confirm(`${reservation.dates.join(", ")} 예약 전체를 취소할까요?`)) return;
          mutate(`/api/admin/reservations/${encodeURIComponent(reservation.id)}`, "DELETE", undefined,
            "예약이 취소되었습니다.", () => { if (state.original?.id === reservation.id) resetReservation(); });
        });
        row.append(edit, cancel);
        card.append(row);
      }
      list.append(card);
    }
    if (!state.reservations.length) list.append(node("p", "이달의 예약이 없습니다.", "help-text"));
    const blocked = el("blocked-list");
    blocked.replaceChildren();
    for (const day of state.calendar?.dates || []) {
      if (day.status !== "BLOCKED") continue;
      const button = node("button", `${day.date} 차단 해제`);
      button.type = "button";
      button.dataset.date = day.date;
      button.dataset.write = "true";
      button.addEventListener("click", () => {
        if (!canWrite()) return;
        if (!window.confirm(`${day.date}의 차단을 해제할까요?`)) return;
        mutate(`/api/admin/blocked-dates/${day.date}`, "DELETE", undefined, "차단이 해제되었습니다.");
      });
      blocked.append(button);
    }
    if (!blocked.children.length) blocked.append(node("p", "이달에 해제 가능한 차단일이 없습니다.", "help-text"));
    const logs = el("logs");
    logs.replaceChildren();
    for (const log of state.logs) {
      const item = node("li");
      item.dataset.id = String(log.id);
      item.append(node("p", `${log.action} · ${log.created_at}`), node("p", JSON.stringify(log.detail)));
      logs.append(item);
    }
    if (!state.logs.length) logs.append(node("li", "표시할 이력이 없습니다."));
    el("logs-page").textContent = `${state.page} / ${Math.max(1, Math.ceil(state.total / pageSize))} 페이지 · ${state.total}건`;
    el("current-horizon").textContent = state.horizon === null ? "" : `현재 기간: 오늘 포함 ${state.horizon}일`;
  }

  class RequestError extends Error {
    constructor(status, code, dates = []) {
      super("Request failed");
      this.status = status;
      this.code = code;
      this.dates = Array.isArray(dates) ? dates.filter((date) => typeof date === "string" && datePattern.test(date)) : [];
    }
  }

  async function requestJSON(url, method = "GET", body) {
    const controller = new AbortController();
    pendingRequests.add(controller);
    const timer = window.setTimeout(() => controller.abort(), 20000);
    const headers = { Accept: "application/json" };
    if (method !== "GET") headers["X-CSRF-Token"] = state.csrf;
    if (body !== undefined) headers["Content-Type"] = "application/json";
    try {
      const response = await fetch(url, {
        method, headers, body: body === undefined ? undefined : JSON.stringify(body),
        cache: "no-store", credentials: "same-origin", redirect: "error", signal: controller.signal,
      });
      let data;
      try { data = await response.json(); } catch { throw new RequestError(response.ok ? 0 : response.status); }
      if (state.disposed) throw new RequestError(0);
      if (!response.ok) throw new RequestError(response.status, data?.code, data?.dates);
      if (!data || typeof data !== "object" || Array.isArray(data)) throw new RequestError(0);
      return data;
    } finally { window.clearTimeout(timer); pendingRequests.delete(controller); }
  }

  function handleError(error, writing = false) {
    if (error.status === 403) {
      state.csrf = null;
      state.expires = 0;
      return "관리자 접근 권한 또는 요청 확인이 만료되었습니다 (403). 인증서와 접속 환경을 확인한 뒤 ‘접근 다시 확인’을 눌러 주세요. 자동으로 다시 제출하지 않습니다.";
    }
    if (error.status === 409) return `변경할 수 없거나 다른 예약·차단과 날짜가 충돌합니다 (409). ${error.dates.length ? `충돌 날짜: ${error.dates.join(", ")}. ` : ""}입력은 유지됩니다. 전체 새로고침으로 최신 상태를 확인해 주세요.`;
    if ([400, 413, 415, 422].includes(error.status)) return "입력값과 날짜·예약 가능 기간을 확인해 주세요 (422). 이름은 1~40자, 메모는 200자까지입니다. 입력은 유지됩니다.";
    if (error.status === 404) return "대상을 찾을 수 없습니다 (404). 전체 새로고침으로 최신 상태를 확인해 주세요.";
    if (error.status === 429) return "요청이 너무 많습니다 (429). 잠시 후 직접 다시 시도해 주세요. 입력은 유지됩니다.";
    if (error.status === 503) return "일시적으로 처리할 수 없습니다 (503). 잠시 후 직접 다시 시도해 주세요. 입력은 유지됩니다.";
    if (writing) {
      state.ready = false;
      return "처리 결과를 확인할 수 없습니다. 이미 반영되었을 수 있으므로 전체 새로고침으로 결과를 확인해 주세요. 입력은 유지되며 자동으로 다시 제출하지 않습니다.";
    }
    return "관리 정보를 불러오지 못했습니다. 연결을 확인한 뒤 전체 새로고침을 눌러 주세요. 입력은 유지됩니다.";
  }

  async function acquireCSRF() {
    const result = await requestJSON("/api/admin/csrf");
    if (state.disposed) throw new RequestError(0);
    if (typeof result.csrf_token !== "string" || !result.csrf_token || result.expires_in !== 1800) throw new RequestError(0);
    state.csrf = result.csrf_token;
    state.expires = performance.now() + result.expires_in * 1000;
  }

  async function fetchSnapshot(month = state.month, page = state.page) {
    const [calendar, reservations, logs, settings] = await Promise.all([
      requestJSON(`/api/calendar?month=${month}`),
      requestJSON(`/api/admin/reservations?month=${month}&status=ALL`),
      requestJSON(`/api/admin/logs?page=${page}&page_size=${pageSize}`),
      requestJSON("/api/admin/settings"),
    ]);
    if (state.disposed) throw new RequestError(0);
    if (!Array.isArray(calendar.dates) || calendar.dates.length !== monthLength(month)
        || ![calendar.today, calendar.min_date, calendar.max_date].every((value) => typeof value === "string" && datePattern.test(value))
        || !calendar.dates.every((day, index) => day.date === `${month}-${String(index + 1).padStart(2, "0")}` && labels[day.status])
        || !Array.isArray(reservations.reservations) || !Array.isArray(logs.logs)
        || !Number.isInteger(settings.booking_horizon_days)) throw new RequestError(0);
    state.month = month;
    state.calendar = calendar;
    state.reservations = reservations.reservations;
    state.logs = logs.logs;
    state.page = logs.page;
    state.total = logs.total;
    state.horizon = settings.booking_horizon_days;
    if (!state.horizonDirty) el("booking-horizon").value = String(state.horizon);
    state.ready = true;
    renderCalendar();
    renderLists();
  }

  async function refresh(month = state.month, page = state.page, renew = false) {
    if (state.busy || state.disposed) return;
    state.busy = true;
    syncControls();
    announce("관리 정보를 불러오는 중입니다.");
    try {
      if (renew) await acquireCSRF();
      await fetchSnapshot(month, page);
      announce(renew ? "접근을 확인하고 최신 정보를 불러왔습니다. 변경은 직접 다시 제출해 주세요." : "최신 정보를 불러왔습니다. 작성 중인 입력은 유지됩니다.");
    } catch (error) {
      state.ready = false;
      announce(handleError(error), "error");
    } finally { state.busy = false; syncControls(); }
  }

  function canWrite() {
    if (state.busy || !state.ready || state.disposed) return false;
    if (!state.csrf || performance.now() >= state.expires) {
      state.csrf = null;
      announce("요청 확인이 만료되었습니다. ‘접근 다시 확인’을 누른 뒤 직접 다시 제출해 주세요.", "error");
      syncControls();
      return false;
    }
    return true;
  }

  async function mutate(url, method, body, message, onSuccess = () => {}) {
    if (!canWrite()) return;
    state.busy = true;
    syncControls();
    announce("변경을 처리하고 있습니다. 잠시 기다려 주세요.");
    try {
      const result = await requestJSON(url, method, body);
      if (state.disposed) throw new RequestError(0);
      if (url === "/api/admin/settings" ? result.booking_horizon_days !== body.booking_horizon_days
        : !["CONFIRMED", "CANCELLED", "BLOCKED", "AVAILABLE"].includes(result.status)) throw new RequestError(0);
      onSuccess();
      try {
        await fetchSnapshot(state.month, 1);
        announce(message, "success");
      } catch (error) {
        state.ready = false;
        announce(`${message} 변경은 완료했지만 조회에 실패했습니다. ${handleError(error)} 같은 변경을 다시 제출하지 마세요.`, "error");
      }
    } catch (error) { announce(handleError(error, true), "error"); }
    finally {
      state.busy = false;
      syncControls();
      if (!state.disposed) el("status").focus();
    }
  }

  el("reservation-form").addEventListener("submit", (event) => {
    event.preventDefault();
    if (!canWrite()) return;
    const dates = validDates("dates");
    const name = el("name").value.trim();
    const memo = el("memo").value;
    if (!dates || !name || [...name].length > 40 || [...memo].length > 200) {
      announce("전체 날짜와 이름·메모를 확인해 주세요. 이름은 1~40자, 메모는 200자까지이며 날짜는 중복할 수 없습니다.", "error");
      return;
    }
    const original = state.original;
    mutate(`/api/admin/reservations${original ? `/${encodeURIComponent(original.id)}` : ""}`,
      original ? "PATCH" : "POST", { name, memo, dates }, original ? "예약이 수정되었습니다." : "예약이 등록되었습니다.", resetReservation);
  });
  el("block-form").addEventListener("submit", (event) => {
    event.preventDefault();
    if (!canWrite()) return;
    const dates = validDates("block-dates");
    const memo = el("block-memo").value;
    if (!dates || [...memo].length > 200) {
      announce("차단 날짜와 메모를 확인해 주세요. 메모는 200자까지이며 날짜는 중복할 수 없습니다.", "error");
      return;
    }
    mutate("/api/admin/blocked-dates", "POST", { dates, memo }, "차단이 등록되었습니다.", () => el("block-form").reset());
  });
  el("settings-form").addEventListener("submit", (event) => {
    event.preventDefault();
    if (!canWrite()) return;
    const value = Number(el("booking-horizon").value);
    if (!Number.isInteger(value) || value < 1 || value > 366) {
      announce("예약 가능 기간은 1~366 사이의 정수로 입력해 주세요.", "error");
      return;
    }
    mutate("/api/admin/settings", "PATCH", { booking_horizon_days: value }, "예약 가능 기간이 저장되었습니다.", () => { state.horizonDirty = false; });
  });
  for (const id of ["name", "memo", "dates"]) el(id).addEventListener("input", () => { renderDraft(); renderCalendar(); syncControls(); });
  el("booking-horizon").addEventListener("input", () => { state.horizonDirty = true; });
  el("new-reservation").addEventListener("click", () => { if (!state.busy) { resetReservation(); syncControls(); el("name").focus(); } });
  el("prev-month").addEventListener("click", () => refresh(shiftMonth(-1)));
  el("next-month").addEventListener("click", () => refresh(shiftMonth(1)));
  el("refresh-data").addEventListener("click", () => refresh());
  el("renew-csrf").addEventListener("click", () => refresh(state.month, state.page, true));
  el("logs-prev").addEventListener("click", () => refresh(state.month, state.page - 1));
  el("logs-next").addEventListener("click", () => refresh(state.month, state.page + 1));
  window.addEventListener("pagehide", () => {
    state.disposed = true;
    for (const controller of pendingRequests) controller.abort();
    state.csrf = null;
    state.expires = 0;
    state.original = null;
    state.reservations = [];
    state.logs = [];
    state.calendar = null;
    state.ready = false;
    state.busy = true;
    for (const control of app.querySelectorAll("input, textarea")) control.value = "";
    for (const id of ["reservation-list", "logs", "edit-original", "edit-diff", "calendar-grid", "blocked-list", "status"]) el(id).replaceChildren();
    syncControls();
  });
  window.addEventListener("pageshow", (event) => { if (event.persisted) window.location.reload(); });
  renderDraft();
  renderCalendar();
  refresh(state.month, 1, true);
})();
