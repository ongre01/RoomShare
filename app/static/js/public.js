/* Calendar keys are Seoul calendar strings, never browser Date timestamps. */
"use strict";

(() => {
  const el = (id) => document.getElementById(id);
  const app = el("calendar-app");
  if (!app) return;
  const state = {
    month: app.dataset.initialMonth,
    selected: new Set(),
    calendar: null,
    today: null,
    loading: false,
    writing: false,
    request: 0,
    needsRefresh: false,
  };
  const datePattern = /^\d{4}-\d{2}-\d{2}$/;
  const statusLabels = {
    AVAILABLE: "가능", RESERVED: "예약", BLOCKED: "불가", PAST: "과거", OUTSIDE: "기간 밖",
  };

  function announce(id, message, kind = "info") {
    el(id).textContent = message;
    el(id).dataset.kind = kind;
  }

  function monthLength(month) {
    const [year, number] = month.split("-").map(Number);
    return [31, year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0) ? 29 : 28,
      31, 30, 31, 30, 31, 31, 30, 31, 30, 31][number - 1];
  }

  function firstWeekday(month) {
    const [year, number] = month.split("-").map(Number);
    const previousYear = year - 1;
    let days = previousYear * 365 + Math.floor(previousYear / 4)
      - Math.floor(previousYear / 100) + Math.floor(previousYear / 400);
    for (let m = 1; m < number; m += 1) {
      days += monthLength(`${String(year).padStart(4, "0")}-${String(m).padStart(2, "0")}`);
    }
    return (days + 1) % 7;
  }

  function shiftMonth(month, amount) {
    const [year, number] = month.split("-").map(Number);
    const index = year * 12 + number - 1 + amount;
    if (index < 12 || index >= 120000) return month;
    return `${String(Math.floor(index / 12)).padStart(4, "0")}-${String(index % 12 + 1).padStart(2, "0")}`;
  }

  function dateStatus(day, calendar) {
    if (day.status !== "AVAILABLE") return day.status;
    if (day.date < calendar.min_date || day.date > calendar.max_date) return "OUTSIDE";
    return "AVAILABLE";
  }

  function syncControls() {
    el("prev-month").disabled = state.writing || state.month === "0001-01";
    el("next-month").disabled = state.writing || state.month === "9999-12";
    el("today-month").disabled = state.writing;
    el("refresh-calendar").disabled = state.writing || state.loading;
    el("clear-selection").disabled = state.writing || state.selected.size === 0;
    el("reserve-button").disabled = state.writing || state.loading || state.needsRefresh
      || !state.calendar || state.selected.size === 0 || !el("reservation-success").hidden;
    el("cancel-button").disabled = state.writing;
    for (const id of ["name", "memo", "cancel-id", "cancel-token", "cancel-confirm"]) {
      el(id).disabled = state.writing;
    }
    el("reservation-form").setAttribute("aria-busy", String(state.writing));
    el("cancellation-form").setAttribute("aria-busy", String(state.writing));
  }

  function renderSelection() {
    const list = el("selected-dates");
    list.replaceChildren();
    el("selected-count").textContent = String(state.selected.size);
    for (const date of [...state.selected].sort()) {
      const item = document.createElement("li");
      const text = document.createElement("span");
      text.textContent = date;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.textContent = "해제";
      remove.setAttribute("aria-label", `${date} 선택 해제`);
      remove.disabled = state.writing;
      remove.addEventListener("click", () => {
        if (state.writing) return;
        state.selected.delete(date);
        render();
        // The removed button no longer exists; keep keyboard focus usable.
        const day = el("calendar-grid").querySelector(`[data-date="${date}"]`);
        if (day && !day.disabled) day.focus();
        else el("clear-selection").disabled ? el("name").focus() : el("clear-selection").focus();
      });
      item.append(text, remove);
      list.append(item);
    }
    syncControls();
  }

  function renderCalendar() {
    const [year, month] = state.month.split("-").map(Number);
    el("month-title").textContent = `${year}년 ${month}월`;
    el("month-title").dataset.month = state.month;
    const grid = el("calendar-grid");
    grid.setAttribute("aria-busy", String(state.loading));
    // Preserve focus on a selected day when replacing its DOM node.
    const focusDate = grid.contains(document.activeElement) ? document.activeElement.dataset.date : null;
    grid.replaceChildren();
    if (!state.calendar) return;
    for (let i = 0; i < firstWeekday(state.month); i += 1) {
      const spacer = document.createElement("span");
      spacer.className = "calendar-empty";
      spacer.setAttribute("aria-hidden", "true");
      grid.append(spacer);
    }
    for (const day of state.calendar.dates) {
      const status = dateStatus(day, state.calendar);
      const selected = state.selected.has(day.date);
      const button = document.createElement("button");
      button.type = "button";
      button.className = "day";
      button.dataset.date = day.date;
      button.dataset.status = status;
      button.disabled = state.writing || state.loading || status !== "AVAILABLE";
      button.setAttribute("aria-pressed", String(selected));
      button.setAttribute("aria-label", `${day.date} ${selected ? "선택됨" : statusLabels[status]}`);
      if (day.date === state.today) button.setAttribute("aria-current", "date");
      const number = document.createElement("span");
      number.className = "day-number";
      number.textContent = String(Number(day.date.slice(-2)));
      const label = document.createElement("span");
      label.className = "day-status";
      label.textContent = selected ? "선택" : statusLabels[status];
      button.append(number, label);
      button.addEventListener("click", () => {
        if (button.disabled || state.writing) return;
        selected ? state.selected.delete(day.date) : state.selected.add(day.date);
        render();
      });
      grid.append(button);
    }
    if (focusDate) grid.querySelector(`[data-date="${focusDate}"]`)?.focus();
  }

  function render() {
    renderCalendar();
    renderSelection();
  }

  async function requestJSON(url, body) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetch(url, {
        method: body === undefined ? "GET" : "POST",
        cache: "no-store", credentials: "same-origin", redirect: "error",
        headers: body === undefined ? { Accept: "application/json" }
          : { Accept: "application/json", "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal,
      });
      const data = await response.json();
      return { status: response.status, data };
    } finally {
      window.clearTimeout(timeout);
    }
  }

  function validateCalendar(data, month) {
    if (!data || ![data.today, data.min_date, data.max_date].every(
      (value) => typeof value === "string" && datePattern.test(value),
    ) || !Array.isArray(data.dates) || data.dates.length !== monthLength(month)) return false;
    return data.dates.every((day, index) => day.date === `${month}-${String(index + 1).padStart(2, "0")}`
      && ["AVAILABLE", "RESERVED", "BLOCKED", "PAST"].includes(day.status));
  }

  function reconcile(data) {
    state.today = data.today;
    el("booking-window").textContent = `예약 가능 기간 ${data.min_date} ~ ${data.max_date} · 한국시간 기준`;
    const removed = [];
    for (const date of state.selected) {
      const day = data.dates.find((item) => item.date === date);
      if (date < data.min_date || date > data.max_date || (day && dateStatus(day, data) !== "AVAILABLE")) {
        state.selected.delete(date);
        removed.push(date);
      }
    }
    return removed;
  }

  function errorMessage(status, operation) {
    if (status === 400 || status === 413 || status === 415 || status === 422) {
      return "입력값과 선택 날짜를 확인해 주세요. 이름은 1~40자, 메모는 200자까지 입력할 수 있습니다.";
    }
    if (status === 429) return "요청이 너무 많습니다. 잠시 후 직접 다시 시도해 주세요.";
    if (status === 503) return "일시적으로 처리할 수 없습니다. 잠시 후 직접 다시 시도해 주세요.";
    if (operation === "cancel" && status === 404) return "예약 ID 또는 취소 토큰을 확인해 주세요.";
    if (operation === "cancel" && status === 409) return "오늘 또는 과거 날짜를 포함한 예약은 취소할 수 없습니다.";
    if (operation === "calendar") return "달력을 불러오지 못했습니다. 잠시 후 새로고침해 주세요.";
    return unknownMessage(operation);
  }

  function unknownMessage(operation) {
    return `${operation === "cancel" ? "취소" : "예약"} 결과를 확인할 수 없습니다. 이미 처리되었을 수 있습니다. `
      + "자동으로 다시 제출하지 않습니다. 달력을 새로 확인하고, 결과가 불분명하면 관리자에게 문의해 주세요.";
  }

  async function loadMonth(month, followToday = false) {
    if (state.writing) return;
    const request = ++state.request;
    state.month = month;
    state.calendar = null;
    state.loading = true;
    announce("calendar-status", "달력을 불러오는 중입니다.");
    render();
    try {
      const { status, data } = await requestJSON(`/api/calendar?month=${month}`);
      if (request !== state.request) return;
      if (status !== 200 || !validateCalendar(data, month)) {
        announce("calendar-status", errorMessage(status, "calendar"), "error");
        return;
      }
      // An open tab can cross KST midnight/month-end. "Today" must follow
      // the newly returned server date, not the date cached in that tab.
      if (followToday && data.today.slice(0, 7) !== month) {
        await loadMonth(data.today.slice(0, 7));
        return;
      }
      state.calendar = data;
      const removed = reconcile(data);
      announce("calendar-status", removed.length
        ? `예약할 수 없어 선택에서 해제한 날짜: ${removed.sort().join(", ")}`
        : `${month} 달력을 불러왔습니다.`, removed.length ? "error" : "info");
    } catch {
      if (request === state.request) announce("calendar-status", "달력을 불러오지 못했습니다. 연결을 확인하고 새로고침해 주세요.", "error");
    } finally {
      if (request === state.request) {
        state.loading = false;
        render();
      }
    }
  }

  // Mutations freeze navigation. Each relevant month is re-read exactly once;
  // an old navigation response cannot overwrite these newer snapshots.
  async function refreshMonths(dates = []) {
    ++state.request;
    state.loading = true;
    state.calendar = null;
    render();
    const months = new Set([state.month, ...[...state.selected, ...dates].map((date) => date.slice(0, 7))]);
    const results = await Promise.all([...months].map(async (month) => {
      try {
        const result = await requestJSON(`/api/calendar?month=${month}`);
        return result.status === 200 && validateCalendar(result.data, month) ? { month, data: result.data } : null;
      } catch { return null; }
    }));
    const removed = [];
    for (const result of results) {
      if (!result) continue;
      removed.push(...reconcile(result.data));
      if (result.month === state.month) state.calendar = result.data;
    }
    state.needsRefresh = results.some((result) => !result);
    state.loading = false;
    announce("calendar-status", state.needsRefresh
      ? "일부 달력을 갱신하지 못했습니다. 예약 전에 달력 새로고침을 눌러 주세요."
      : removed.length ? `예약할 수 없어 선택에서 해제한 날짜: ${[...new Set(removed)].sort().join(", ")}`
        : "달력을 최신 상태로 갱신했습니다.", state.needsRefresh || removed.length ? "error" : "info");
    render();
  }

  function hideSuccess() {
    el("success-id").value = "";
    el("success-token").value = "";
    el("reservation-success").hidden = true;
    announce("copy-status", "");
    syncControls();
  }

  el("prev-month").addEventListener("click", () => loadMonth(shiftMonth(state.month, -1)));
  el("next-month").addEventListener("click", () => loadMonth(shiftMonth(state.month, 1)));
  el("today-month").addEventListener("click", () => loadMonth((state.today || app.dataset.initialMonth).slice(0, 7), true));
  el("refresh-calendar").addEventListener("click", async () => {
    if (state.writing || state.loading) return;
    state.writing = true;
    await refreshMonths();
    state.writing = false;
    render();
  });
  el("clear-selection").addEventListener("click", () => {
    if (state.writing) return;
    state.selected.clear();
    render();
    el("name").focus();
  });

  el("reservation-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (state.writing || state.loading || state.needsRefresh || !state.calendar || !el("reservation-success").hidden) return;
    const name = el("name").value.trim();
    const memo = el("memo").value;
    const dates = [...state.selected].sort();
    if (!dates.length || !name || [...name].length > 40 || [...memo].length > 200) {
      announce("reservation-status", "날짜를 선택하고 입력값을 확인해 주세요. 이름은 1~40자, 메모는 200자까지입니다.", "error");
      if (!name || [...name].length > 40) el("name").focus();
      else if ([...memo].length > 200) el("memo").focus();
      return;
    }
    state.writing = true;
    announce("reservation-status", "예약을 처리하고 있습니다. 잠시 기다려 주세요.");
    render();
    try {
      const { status, data } = await requestJSON("/api/reservations", { name, memo, dates });
      if (status === 201 && data?.status === "CONFIRMED" && typeof data.id === "string"
          && typeof data.cancel_token === "string" && data.id && data.cancel_token) {
        state.selected.clear();
        el("success-id").value = data.id;
        el("success-token").value = data.cancel_token;
        el("reservation-success").hidden = false;
        announce("reservation-status", "예약이 확정되었습니다. 예약 ID와 취소 토큰을 함께 보관해 주세요.", "success");
        el("success-id").focus();
        await refreshMonths(dates);
      } else if (status === 409 && data?.code === "DATE_CONFLICT") {
        const conflicts = Array.isArray(data.dates) ? data.dates.filter((date) => dates.includes(date)) : [];
        for (const date of conflicts) state.selected.delete(date);
        announce("reservation-status", `예약할 수 없는 날짜입니다: ${conflicts.join(", ")}. 가능한 날짜 선택은 유지됩니다. 확인 후 직접 다시 제출해 주세요.`, "error");
        await refreshMonths(dates);
      } else {
        announce("reservation-status", errorMessage(status, "reserve"), "error");
      }
    } catch {
      announce("reservation-status", unknownMessage("reserve"), "error");
    } finally {
      state.writing = false;
      render();
    }
  });

  el("cancellation-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (state.writing) return;
    const id = el("cancel-id").value.trim();
    const cancel_token = el("cancel-token").value.trim();
    if (!id || !cancel_token || !el("cancel-confirm").checked) {
      announce("cancel-status", "예약 ID와 취소 토큰을 입력하고 예약 전체 취소에 동의해 주세요.", "error");
      return;
    }
    state.writing = true;
    ++state.request;
    if (state.loading) {
      announce("calendar-status", "달력 조회를 중단했습니다. 취소 처리 후 달력 새로고침을 눌러 주세요.");
    }
    state.loading = false;
    announce("cancel-status", "취소를 처리하고 있습니다. 잠시 기다려 주세요.");
    render();
    try {
      const { status, data } = await requestJSON("/api/reservations/cancel", { id, cancel_token });
      if (status === 200 && data?.status === "CANCELLED" && data.id === id) {
        announce("cancel-status", "예약 전체가 취소되었습니다.", "success");
        el("cancel-token").value = "";
        el("cancel-confirm").checked = false;
        if (el("success-id").value === id) hideSuccess();
        await refreshMonths();
      } else announce("cancel-status", errorMessage(status, "cancel"), "error");
    } catch {
      announce("cancel-status", unknownMessage("cancel"), "error");
    } finally {
      state.writing = false;
      render();
    }
  });

  el("copy-reservation").addEventListener("click", async () => {
    if (el("reservation-success").hidden) return;
    try {
      await navigator.clipboard.writeText(`예약 ID: ${el("success-id").value}\n취소 토큰: ${el("success-token").value}`);
      announce("copy-status", "복사했습니다. 안전한 곳에 직접 보관해 주세요.", "success");
    } catch {
      announce("copy-status", "복사하지 못했습니다. 예약 ID와 취소 토큰을 각각 선택해 직접 복사해 주세요.", "error");
    }
  });
  el("dismiss-success").addEventListener("click", () => { hideSuccess(); el("name").focus(); });
  // Remove credentials before a browser back/forward cache snapshot as well.
  window.addEventListener("pagehide", () => { hideSuccess(); el("cancel-token").value = ""; });
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) {
      hideSuccess();
      el("cancel-token").value = "";
      loadMonth(state.month);
    }
  });
  loadMonth(state.month);
})();
