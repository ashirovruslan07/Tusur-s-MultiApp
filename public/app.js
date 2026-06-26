const app = document.querySelector("#app");

const routes = {
  dashboard: { label: "Главная", api: "/api/dashboard" },
  schedule: { label: "Расписание", api: "/api/schedule" },
  finance: { label: "Финансы", api: "/api/finance" },
  workouts: { label: "Тренировки", api: "/api/workouts" },
  planner: { label: "Ежедневник", api: "/api/planner" },
  portfolio: { label: "Портфолио", api: "/api/portfolio" },
  profile: { label: "Профиль", api: "/api/profile" },
  settings: { label: "Настройки", api: "/api/settings" },
  adminSchedule: { label: "Админ расписания", api: "/api/admin/schedule", admin: true }
};

const state = {
  user: null,
  profile: null,
  authMode: "login",
  route: "dashboard",
  data: null,
  notice: "",
  toasts: [],
  syncProgress: null,
  adminFilters: { faculty: "", course: "" },
  adminActionFilters: { groupsFaculty: "", pointFaculty: "", massFaculty: "" },
  scheduleWeekStart: currentWeekMondayIso(),
  profileFilters: { facultyId: "", courseNumber: "" },
  financeFilters: { type: "all", account: "all", search: "" },
  plannerFilters: { status: "active", priority: "all" }
};

const weekLabels = {
  "четная": "Четная",
  "нечетная": "Нечетная",
  "обычная": "Обычная",
  "числитель": "Числитель",
  "знаменатель": "Знаменатель"
};

const dayNames = ["", "Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"];
const dayShort = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
const monthNames = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
const monthTitleNames = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"];

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function money(value) {
  return `${Number(value || 0).toLocaleString("ru-RU")} ₽`;
}

function dateRu(value) {
  if (!value) return "";
  const [year, month, day] = String(value).slice(0, 10).split("-");
  return year && month && day ? `${day}.${month}.${year}` : value;
}

function parseIsoDate(value) {
  const [year, month, day] = String(value || "").slice(0, 10).split("-").map(Number);
  if (!year || !month || !day) return null;
  return new Date(year, month - 1, day);
}

function formatIsoDate(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function addDaysIso(value, offset) {
  const date = parseIsoDate(value);
  if (!date) return "";
  date.setDate(date.getDate() + offset);
  return formatIsoDate(date);
}

function currentWeekMondayIso() {
  const date = new Date();
  const day = date.getDay() || 7;
  date.setDate(date.getDate() - day + 1);
  return formatIsoDate(date);
}

function scheduleWeekStart(week = {}) {
  return parseIsoDate(week.starts_at) ? String(week.starts_at).slice(0, 10) : currentWeekMondayIso();
}

function scheduleDayLabel(value) {
  const date = parseIsoDate(value);
  if (!date) return { dateText: "", dayText: "" };
  return {
    dateText: `${date.getDate()} ${monthNames[date.getMonth()]}`,
    dayText: dayNames[date.getDay() || 7]
  };
}

function scheduleMonthLabel(startIso) {
  const date = parseIsoDate(startIso);
  return date ? `${monthTitleNames[date.getMonth()]} ${date.getFullYear()}` : "";
}

function scheduleWeekRangeLabel(startIso) {
  const start = parseIsoDate(startIso);
  const endIso = addDaysIso(startIso, 6);
  const end = parseIsoDate(endIso);
  if (!start || !end) return "";
  return `${start.getDate()} ${monthNames[start.getMonth()]} - ${end.getDate()} ${monthNames[end.getMonth()]}`;
}

function scheduleWeekRangeShort(startIso, endIso) {
  const start = parseIsoDate(startIso);
  const end = parseIsoDate(endIso || addDaysIso(startIso, 6));
  if (!start || !end) return "";
  return `${dateRu(formatIsoDate(start))} - ${dateRu(formatIsoDate(end))}`;
}

function weekLabel(value) {
  return weekLabels[value] || value || "Обычная";
}

function priorityLabel(value) {
  return { high: "Высокий", medium: "Средний", low: "Низкий" }[value] || value;
}

function statusLabel(value) {
  return { planned: "Запланировано", in_progress: "В работе", done: "Выполнено", completed: "Завершен" }[value] || value;
}

function syncTriggerLabel(value) {
  return {
    manual: "Массовое обновление",
    "manual-one": "Одна группа",
    "manual-auto": "Автообновление вручную",
    auto: "Автообновление"
  }[value] || value;
}

function syncStatusLabel(value) {
  return {
    running: "Выполняется",
    success: "Успешно",
    partial: "Частично",
    failed: "Ошибка"
  }[value] || value;
}

function initials(name) {
  return String(name || "M")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase();
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }

  const response = await fetch(path, {
    credentials: "include",
    ...options,
    headers
  });
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : await response.text();

  if (response.status === 401) {
    state.user = null;
    renderAuth();
  }

  if (!response.ok) {
    const message = payload?.detail || payload?.error || "Запрос не выполнен";
    throw new Error(message);
  }

  return payload;
}

function routeFromHash() {
  const value = location.hash.replace("#", "");
  if (!routes[value]) return "dashboard";
  if (state.user?.is_admin && !routes[value].admin) return "adminSchedule";
  if (routes[value].admin && !state.user?.is_admin) return "dashboard";
  return value;
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme === "dark" ? "dark" : "light";
}

async function init() {
  try {
    const result = await api("/api/auth/me");
    state.user = result.user;
    state.profile = result.profile;
    applyTheme(result.settings?.theme);
    if (!state.user) {
      applyTheme("light");
      renderAuth();
      return;
    }
    await loadRoute(routeFromHash());
  } catch (error) {
    state.notice = error.message;
    renderAuth();
  }
}

function renderAuth() {
  const isLogin = state.authMode === "login";
  app.innerHTML = `
    <div class="auth-screen">
      <section class="auth-panel">
        <div class="auth-title">
          <div class="brand-logo">M</div>
          <h1 style="margin-top:18px;">MultiApp</h1>
          <p>Личный кабинет студента ТУСУР</p>
        </div>

        ${state.notice ? `<div class="notice section-gap">${escapeHtml(state.notice)}</div>` : ""}

        <div class="auth-tabs">
          <button class="btn ${isLogin ? "btn-primary" : "btn-secondary"}" data-auth-tab="login">Вход</button>
          <button class="btn ${!isLogin ? "btn-primary" : "btn-secondary"}" data-auth-tab="register">Регистрация</button>
        </div>

        <form data-auth="${isLogin ? "login" : "register"}">
          <div class="form-grid">
            ${!isLogin ? `
              <div class="field field-full">
                <label>ФИО</label>
                <input name="full_name" required placeholder="Дмитрий Иванов" />
              </div>
            ` : ""}
            <div class="field field-full">
              <label>Email</label>
              <input name="email" type="email" required autocomplete="email" placeholder="student@example.com" />
            </div>
            <div class="field field-full">
              <label>Пароль</label>
              <input name="password" type="password" required minlength="6" autocomplete="${isLogin ? "current-password" : "new-password"}" />
            </div>
            ${!isLogin ? `
              <div class="field field-full">
                <label>Повторите пароль</label>
                <input name="password_confirm" type="password" required minlength="6" autocomplete="new-password" />
              </div>
            ` : ""}
          </div>
          <div class="form-actions">
            <button class="btn btn-primary" type="submit">${isLogin ? "Войти" : "Создать аккаунт"}</button>
          </div>
        </form>
      </section>
      <section class="auth-visual">
        <div class="auth-card">
          <div class="grid grid-2">
            <div class="kpi kpi-primary"><h3>Расписание</h3><strong>ТУСУР</strong><small>Актуальные пары выбранной группы</small></div>
            <div class="kpi kpi-green"><h3>Финансы</h3><strong>Баланс</strong><small>Счета, категории, операции</small></div>
            <div class="kpi kpi-cyan"><h3>Тренировки</h3><strong>Планы</strong><small>Упражнения и журнал</small></div>
            <div class="kpi kpi-purple"><h3>Портфолио</h3><strong>Навыки</strong><small>Проекты и сертификаты</small></div>
          </div>
        </div>
      </section>
    </div>
  `;
}

function renderShell(content) {
  const profile = state.profile || {};
  const active = state.route;
  app.innerHTML = `
    <div class="app-shell">
      <aside class="sidebar">
        <div class="brand">
          <div class="brand-logo">M</div>
          <div>
            <h1>MultiApp</h1>
            <p>${escapeHtml(profile.group_name || "Студент ТУСУР")}</p>
          </div>
        </div>
        <div class="menu-title">Модули</div>
        <nav class="menu">
          ${Object.entries(routes).filter(([, item]) => state.user?.is_admin ? item.admin : !item.admin).map(([key, item]) => `
            <a class="menu-item ${active === key ? "active" : ""}" href="#${key}">
              <span>${escapeHtml(item.label)}</span>
            </a>
          `).join("")}
        </nav>
        <div class="sidebar-card">
          <h3>${escapeHtml(state.user?.full_name || "Профиль")}</h3>
          <p>${escapeHtml(state.user?.email || "")}</p>
          <button class="btn btn-ghost" data-logout>Выйти</button>
        </div>
      </aside>
      <main class="content">
        ${state.notice ? `<div class="notice">${escapeHtml(state.notice)}</div>` : ""}
        ${content}
      </main>
    </div>
    ${renderToasts()}
  `;
}

function notify(message, type = "success") {
  const id = Date.now() + Math.random();
  state.toasts.push({ id, message, type });
  renderRoute();
  setTimeout(() => {
    state.toasts = state.toasts.filter((toast) => toast.id !== id);
    if (state.user) renderRoute();
  }, 4200);
}

function renderToasts() {
  if (!state.toasts.length && !state.syncProgress) return "";
  return `
    <div class="toast-stack">
      ${state.syncProgress ? renderSyncProgress() : ""}
      ${state.toasts.map((toast) => `<div class="toast toast-${toast.type}">${escapeHtml(toast.message)}</div>`).join("")}
    </div>
  `;
}

function renderSyncProgress() {
  const progress = state.syncProgress || {};
  const percent = Math.max(0, Math.min(100, Number(progress.percent || 0)));
  const details = progress.total ? `${progress.processed || 0} из ${progress.total}` : "";
  return `
    <div class="toast sync-toast">
      <div class="sync-toast-head">
        <strong>${escapeHtml(progress.title || "Синхронизация расписания")}</strong>
        <span>${percent}%${details ? ` · ${escapeHtml(details)}` : ""}</span>
      </div>
      <p>${escapeHtml(progress.message || "Идет обмен данными с расписанием ТУСУР.")}</p>
      <div class="sync-progress" aria-label="Синхронизация выполняется">
        <div class="sync-progress-fill" style="width:${percent}%;"></div>
      </div>
    </div>
  `;
}

function syncProgressForEndpoint(endpoint = "") {
  if (!endpoint.includes("/schedule/")) return null;
  if (endpoint.includes("/sync-all-groups")) {
    return {
      title: "Обновление групп",
      message: "Загружаем списки групп по всем факультетам."
    };
  }
  if (endpoint.includes("/sync-groups")) {
    return {
      title: "Обновление групп факультета",
      message: "Получаем актуальный список групп выбранного факультета."
    };
  }
  if (endpoint.includes("/run-auto")) {
    return {
      title: "Автообновление расписания",
      message: "Запущена синхронизация по настройкам автообновления."
    };
  }
  if (endpoint.includes("/sync-all")) {
    return {
      title: "Массовая синхронизация",
      message: "Обновляем расписание выбранных групп. Это может занять несколько минут."
    };
  }
  if (endpoint.includes("/sync")) {
    return {
      title: "Синхронизация группы",
      message: "Обновляем расписание выбранной группы."
    };
  }
  return null;
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function pollSyncProgress(jobId, fallbackProgress) {
  while (true) {
    await delay(700);
    const progress = await api(`/api/admin/schedule/progress/${jobId}`);
    state.syncProgress = {
      ...fallbackProgress,
      ...progress,
      status: progress.status === "success" ? "Готово" : (progress.status === "failed" ? "Ошибка" : "В процессе"),
      percent: progress.percent ?? 0
    };
    renderRoute();
    if (progress.done) {
      state.syncProgress = null;
      if (progress.error) {
        throw new Error(progress.error);
      }
      return progress.result || progress;
    }
  }
}

async function loadRoute(route) {
  if (routes[route]?.admin && !state.user?.is_admin) {
    route = "dashboard";
    location.hash = "#dashboard";
  }
  if (state.user?.is_admin && !routes[route]?.admin) {
    route = "adminSchedule";
    location.hash = "#adminSchedule";
  }
  state.route = route;
  state.notice = "";
  try {
    const endpoint = route === "schedule"
      ? `${routes[route].api}?week_start=${encodeURIComponent(state.scheduleWeekStart || currentWeekMondayIso())}`
      : routes[route].api;
    state.data = await api(endpoint);
    if (route === "profile") {
      state.profile = state.data.profile;
      const profile = state.data.profile || {};
      if (state.profileFilters.sourceGroupId !== profile.group_id) {
        state.profileFilters = {
          sourceGroupId: profile.group_id,
          facultyId: profile.faculty_id || "",
          courseNumber: profile.course_number || ""
        };
      }
    }
    if (state.data?.settings) applyTheme(state.data.settings.theme);
    renderRoute();
  } catch (error) {
    state.notice = error.message;
    renderRoute();
  }
}

function topbar(title, subtitle, actions = "") {
  return `
    <header class="topbar">
      <div>
        <h2>${escapeHtml(title)}</h2>
        <p>${escapeHtml(subtitle)}</p>
      </div>
      <div class="top-actions">${actions}</div>
    </header>
  `;
}

function renderRoute() {
  const data = state.data || {};
  const html = {
    dashboard: () => renderDashboard(data),
    schedule: () => renderSchedule(data),
    finance: () => renderFinance(data),
    workouts: () => renderWorkouts(data),
    planner: () => renderPlanner(data),
    portfolio: () => renderPortfolio(data),
    profile: () => renderProfile(data),
    settings: () => renderSettings(data),
    adminSchedule: () => renderAdminSchedule(data)
  }[state.route]();
  renderShell(html);
}

function renderDashboard(data) {
  const kpi = data.kpi || {};
  const modules = data.modules || {};
  const widgets = data.widgets || {};
  const feed = data.todayFeed || [];
  const profile = data.profile || {};
  const lessons = widgets.lessons || [];
  const tasks = widgets.tasks || [];
  const events = widgets.events || [];
  const transactions = widgets.transactions || [];
  const workouts = widgets.workouts || [];
  const portfolio = widgets.portfolio || [];
  const accounts = widgets.accounts || [];
  return `
    ${topbar("Главная", `${profile.group_name || "Группа не выбрана"} · ${dateRu(data.dates?.today)}`,
      `<a class="btn btn-primary" href="#schedule">Расписание</a><a class="btn btn-secondary" href="#planner">Добавить задачу</a>`)}
    <section class="grid grid-4">
      <div class="kpi kpi-primary"><h3>Пар сегодня</h3><strong>${kpi.todayLessons || 0}</strong><small>${kpi.firstTodayLesson ? `Первая в ${kpi.firstTodayLesson}` : "На сегодня пар нет"}</small></div>
      <div class="kpi kpi-green"><h3>Баланс</h3><strong>${money(kpi.balance)}</strong><small>Доходы: ${money(kpi.monthlyIncome)} · расходы: ${money(kpi.monthlyExpense)}</small></div>
      <div class="kpi kpi-cyan"><h3>Тренировки</h3><strong>${kpi.weeklyWorkouts || 0}</strong><small>За последние 7 дней</small></div>
      <div class="kpi kpi-purple"><h3>Задачи</h3><strong>${kpi.activeTasks || 0}</strong><small>На сегодня: ${kpi.todayTasks || 0}</small></div>
    </section>

    <section class="dashboard-layout section-gap">
      <div class="card dashboard-main">
        <div class="card-header">
          <div><h3 class="card-title">Ближайшие пары</h3><p class="card-subtitle">${weekLabel(modules.schedule?.weekType)} · ${escapeHtml(modules.schedule?.group || "группа не выбрана")}</p></div>
          <span class="badge badge-primary">${modules.schedule?.tomorrowLessons || 0} завтра</span>
        </div>
        <div class="timeline-list">
          ${lessons.length ? lessons.map((lesson) => `
            <a class="timeline-item" href="#schedule">
              <div class="time-chip">${escapeHtml(lesson.start_time || "")}<span>${escapeHtml(lesson.end_time || "")}</span></div>
              <div class="timeline-main">
                <h4>${escapeHtml(lesson.discipline)}</h4>
                <p>${escapeHtml(dayNames[lesson.day_number] || "")} · ${escapeHtml(lesson.lesson_type || "")} · ${escapeHtml(lesson.auditorium || "аудитория не указана")}</p>
              </div>
            </a>
          `).join("") : `<div class="empty">Ближайшие пары не найдены. Проверьте выбранную группу в профиле.</div>`}
        </div>
      </div>

      <div class="card">
        <div class="card-header"><div><h3 class="card-title">Дела недели</h3><p class="card-subtitle">${modules.planner?.weekEvents || 0} событий, ${modules.planner?.activeTasks || 0} активных задач</p></div></div>
        <div class="list">
          ${[
            ...events.map((event) => ({ title: event.title, note: `${dateRu(event.event_date)} ${event.start_time || ""}`.trim(), badge: "Событие" })),
            ...tasks.map((task) => ({ title: task.title, note: `${dateRu(task.due_date)} · ${priorityLabel(task.priority)}`, badge: "Задача" }))
          ].slice(0, 6).map((item) => `
            <a class="list-item" href="#planner">
              <div class="list-main"><h4>${escapeHtml(item.title)}</h4><p>${escapeHtml(item.note)}</p></div>
              <span class="badge badge-purple">${escapeHtml(item.badge)}</span>
            </a>
          `).join("") || `<div class="empty">На ближайшую неделю нет задач и событий.</div>`}
        </div>
      </div>
    </section>

    <section class="grid grid-3 section-gap">
      <div class="card">
        <div class="card-header"><div><h3 class="card-title">Финансы</h3><p class="card-subtitle">${modules.finance?.activeAccounts || 0} активных счетов</p></div><a class="badge badge-green" href="#finance">Открыть</a></div>
        <div class="list">
          ${accounts.map((account) => `
            <a class="list-item" href="#finance">
              <div class="list-main"><h4>${escapeHtml(account.account_name)}</h4><p>${escapeHtml(account.account_type)} · ${escapeHtml(account.currency)}</p></div>
              <strong>${money(account.balance)}</strong>
            </a>
          `).join("") || `<div class="empty">Счета пока не добавлены.</div>`}
        </div>
        <div class="mini-metrics">
          <div><span>Доходы</span><strong>${money(modules.finance?.income)}</strong></div>
          <div><span>Расходы</span><strong>${money(modules.finance?.expense)}</strong></div>
        </div>
      </div>

      <div class="card">
        <div class="card-header"><div><h3 class="card-title">Тренировки</h3><p class="card-subtitle">План на ближайшие дни</p></div><a class="badge badge-cyan" href="#workouts">${modules.workouts?.completion || 0}%</a></div>
        <div class="list">
          ${workouts.map((plan) => `
            <a class="list-item" href="#workouts">
              <div class="list-main"><h4>${escapeHtml(plan.plan_name)}</h4><p>${escapeHtml(dayNames[plan.day_number] || "")} · ${escapeHtml(plan.type_name || "")}</p></div>
              <span class="badge badge-cyan">${plan.duration_minutes || 0} мин</span>
            </a>
          `).join("") || `<div class="empty">Планы тренировок пока не добавлены.</div>`}
        </div>
      </div>

      <div class="card">
        <div class="card-header"><div><h3 class="card-title">Портфолио</h3><p class="card-subtitle">Заполненность: ${modules.portfolio?.completion || 0}%</p></div><a class="badge badge-purple" href="#portfolio">Открыть</a></div>
        <div class="list">
          ${portfolio.map((item) => `
            <a class="list-item" href="#portfolio">
              <div class="list-main"><h4>${escapeHtml(item.title)}</h4><p>${escapeHtml(item.text || item.kind)} · ${dateRu(item.date)}</p></div>
              <span class="badge badge-neutral">${escapeHtml(item.kind)}</span>
            </a>
          `).join("") || `<div class="empty">Портфолио пока не заполнено.</div>`}
        </div>
      </div>
    </section>

    <section class="grid grid-2 section-gap">
      <div class="card">
        <div class="card-header"><div><h3 class="card-title">Лента на сегодня</h3><p class="card-subtitle">${dateRu(data.dates?.today)}</p></div></div>
        <div class="list">
          ${feed.length ? feed.map((item) => `
            <div class="list-item">
              <div class="list-main"><h4>${escapeHtml(item.title)}</h4><p>${escapeHtml(item.text)}</p></div>
              <span class="badge badge-primary">${escapeHtml(item.kind)}</span>
            </div>
          `).join("") : `<div class="empty">На сегодня нет записей.</div>`}
        </div>
      </div>

      <div class="card">
        <div class="card-header"><div><h3 class="card-title">Последние операции</h3><p class="card-subtitle">Финансовая активность</p></div></div>
        <div class="list">
          ${transactions.map((item) => `
            <a class="list-item" href="#finance">
              <div class="list-main"><h4>${escapeHtml(item.category_name)}</h4><p>${dateRu(item.transaction_date)} · ${escapeHtml(item.account_name)}</p></div>
              <strong class="${item.transaction_type === "income" ? "amount-income" : "amount-expense"}">${item.transaction_type === "income" ? "+" : "-"}${money(item.amount)}</strong>
            </a>
          `).join("") || `<div class="empty">Операций пока нет.</div>`}
        </div>
      </div>
    </section>
  `;
}

function facultySelect(faculties = [], name, selected, actionFilter = "") {
  const filterAttr = actionFilter ? ` data-admin-action-filter="${actionFilter}"` : "";
  return `
    <select name="${name}"${filterAttr}>
      ${faculties.map((faculty) => `
        <option value="${escapeHtml(faculty.site_code || faculty.abbreviation)}" ${(faculty.site_code || faculty.abbreviation) === selected ? "selected" : ""}>
          ${escapeHtml(faculty.full_name)}
        </option>
      `).join("")}
    </select>
  `;
}

function facultyShortName(value) {
  const words = String(value || "").trim().split(/\s+/).filter(Boolean);
  return words.slice(0, 3).join(" ") || value || "";
}

function facultySelectAll(faculties = [], name, selected = "", actionFilter = "") {
  const filterAttr = actionFilter ? ` data-admin-action-filter="${actionFilter}"` : "";
  return `
    <select name="${name}"${filterAttr}>
      <option value="" ${!selected ? "selected" : ""}>Все факультеты</option>
      ${faculties.map((faculty) => `
        <option value="${escapeHtml(faculty.site_code || faculty.abbreviation)}" ${(faculty.site_code || faculty.abbreviation) === selected ? "selected" : ""}>
          ${escapeHtml(faculty.full_name)}
        </option>
      `).join("")}
    </select>
  `;
}

function groupNameSelect(groups = [], name, selected) {
  return `
    <select name="${name}">
      ${groups.map((group) => `<option value="${escapeHtml(group.group_name)}" ${group.group_name === selected ? "selected" : ""}>${escapeHtml(group.group_name)} · ${group.course_number} курс</option>`).join("")}
    </select>
  `;
}

function categorySelect(categories = [], name, selected, type = "") {
  const filtered = type ? categories.filter((category) => category.category_type === type) : categories;
  return `
    <select name="${name}" ${filtered.length ? "" : "disabled"}>
      ${filtered.length ? "" : `<option value="">Нет категорий</option>`}
      ${filtered.map((category) => `<option value="${category.category_id}" ${Number(category.category_id) === Number(selected) ? "selected" : ""}>${escapeHtml(category.category_name)} · ${category.category_type === "income" ? "доход" : "расход"}</option>`).join("")}
    </select>
  `;
}

function accountSelect(accounts = [], name, selected, activeOnly = true) {
  const filtered = activeOnly ? accounts.filter((account) => account.is_active) : accounts;
  return `
    <select name="${name}" ${filtered.length ? "" : "disabled"}>
      ${filtered.length ? "" : `<option value="">Нет активных счетов</option>`}
      ${filtered.map((account) => `<option value="${account.account_id}" ${Number(account.account_id) === Number(selected) ? "selected" : ""}>${escapeHtml(account.account_name)} · ${money(account.balance)}</option>`).join("")}
    </select>
  `;
}

function financeTypeLabel(value) {
  return { income: "Доход", expense: "Расход", transfer: "Перевод" }[value] || value;
}

function financeAmount(value, type) {
  const prefix = type === "income" ? "+" : type === "expense" ? "-" : "";
  const className = type === "income" ? "amount-income" : type === "expense" ? "amount-expense" : "";
  return `<strong class="${className}">${prefix}${money(value)}</strong>`;
}

function filteredFinanceTransactions(transactions = []) {
  const filters = state.financeFilters;
  const search = String(filters.search || "").trim().toLowerCase();
  return transactions.filter((item) => {
    const typeOk = filters.type === "all" || item.transaction_type === filters.type;
    const accountOk = filters.account === "all" || Number(item.account_id) === Number(filters.account);
    const haystack = `${item.category_name || ""} ${item.account_name || ""} ${item.description || ""}`.toLowerCase();
    const searchOk = !search || haystack.includes(search);
    return typeOk && accountOk && searchOk;
  });
}

function renderFinance(data) {
  const accounts = data.accounts || [];
  const categories = data.categories || [];
  const transactions = data.transactions || [];
  const transfers = data.transfers || [];
  const stats = data.stats || {};
  const analytics = data.analytics || {};
  const activeAccounts = accounts.filter((account) => account.is_active);
  const inactiveAccounts = accounts.filter((account) => !account.is_active);
  const filteredTransactions = filteredFinanceTransactions(transactions);
  const expenseByCategory = analytics.expenseByCategory || [];
  return `
    ${topbar("Финансы", "Счета, категории, операции, переводы и месячная аналитика.",
      `<button class="btn btn-secondary" data-action="reload">Обновить</button><a class="btn btn-primary" href="#finance-add">Новая операция</a>`)}
    <section class="grid grid-4">
      <div class="kpi kpi-green"><h3>Общий баланс</h3><strong>${money(stats.balance)}</strong><small>На активных счетах</small></div>
      <div class="kpi kpi-primary"><h3>Доходы</h3><strong>${money(stats.income)}</strong><small>За текущий месяц</small></div>
      <div class="kpi kpi-purple"><h3>Расходы</h3><strong>${money(stats.expense)}</strong><small>За текущий месяц</small></div>
      <div class="kpi kpi-cyan"><h3>Итог месяца</h3><strong>${money(stats.net)}</strong><small>Накопление: ${stats.savingsRate || 0}%</small></div>
    </section>

    <section class="grid grid-2 section-gap">
      <div class="card">
        <div class="card-header"><div><h3 class="card-title">Счета</h3><p class="card-subtitle">${activeAccounts.length} активных, ${inactiveAccounts.length} в архиве</p></div></div>
        <div class="list">
          ${accounts.map((account) => `
            <div class="list-item">
              <div class="list-main"><h4>${escapeHtml(account.account_name)}</h4><p>${escapeHtml(account.account_type)} · ${escapeHtml(account.currency)} · ${account.is_active ? "активен" : "архив"}</p></div>
              <div class="row-actions">
                <strong>${money(account.balance)}</strong>
                ${account.is_active
                  ? `<button class="icon-btn" title="Скрыть счет" data-delete="/api/finance/accounts/${account.account_id}">×</button>`
                  : `<form data-endpoint="/api/finance/accounts/${account.account_id}" data-method="PATCH" data-reload="finance"><input type="hidden" name="is_active" value="1" /><button class="btn btn-secondary" type="submit">Вернуть</button></form>`}
              </div>
            </div>
          `).join("") || `<div class="empty">Счета пока не добавлены.</div>`}
        </div>
      </div>
      <div class="card">
        <div class="card-header"><div><h3 class="card-title">Новый счет</h3><p class="card-subtitle">Баланс попадет в общую сумму.</p></div></div>
        <form data-endpoint="/api/finance/accounts" data-method="POST" data-reload="finance">
          <div class="form-grid">
            <div class="field"><label>Название</label><input name="account_name" required /></div>
            <div class="field"><label>Тип</label><select name="account_type"><option>карта</option><option>наличные</option><option>счет</option><option>вклад</option></select></div>
            <div class="field"><label>Баланс</label><input name="balance" type="number" step="0.01" value="0" /></div>
            <div class="field"><label>Валюта</label><input name="currency" maxlength="8" value="RUB" /></div>
          </div>
          <div class="form-actions"><button class="btn btn-green" type="submit">Добавить счет</button></div>
        </form>
      </div>
    </section>

    <section class="grid grid-3 section-gap" id="finance-add">
      <div class="card">
        <div class="card-header"><div><h3 class="card-title">Операция</h3><p class="card-subtitle">Доход или расход по выбранному счету.</p></div></div>
        <form data-endpoint="/api/finance/transactions" data-method="POST" data-reload="finance">
          <div class="form-grid">
            <div class="field"><label>Счет</label>${accountSelect(accounts, "account_id")}</div>
            <div class="field"><label>Тип</label><select name="transaction_type"><option value="expense">Расход</option><option value="income">Доход</option></select></div>
            <div class="field"><label>Категория</label>${categorySelect(categories, "category_id")}</div>
            <div class="field"><label>Сумма</label><input name="amount" type="number" min="0.01" step="0.01" required /></div>
            <div class="field"><label>Дата</label><input name="transaction_date" type="date" value="${new Date().toISOString().slice(0, 10)}" required /></div>
            <div class="field"><label>Описание</label><input name="description" /></div>
          </div>
          <div class="form-actions"><button class="btn btn-primary" type="submit">Сохранить операцию</button></div>
        </form>
      </div>
      <div class="card">
        <div class="card-header"><div><h3 class="card-title">Перевод</h3><p class="card-subtitle">Движение денег между счетами.</p></div></div>
        <form data-endpoint="/api/finance/transfers" data-method="POST" data-reload="finance">
          <div class="form-grid">
            <div class="field"><label>Откуда</label>${accountSelect(accounts, "from_account_id")}</div>
            <div class="field"><label>Куда</label>${accountSelect(accounts, "to_account_id", activeAccounts[1]?.account_id)}</div>
            <div class="field"><label>Сумма</label><input name="amount" type="number" min="0.01" step="0.01" required /></div>
            <div class="field"><label>Дата</label><input name="transfer_date" type="date" value="${new Date().toISOString().slice(0, 10)}" required /></div>
            <div class="field field-full"><label>Комментарий</label><input name="comment" /></div>
          </div>
          <div class="form-actions"><button class="btn btn-secondary" type="submit">Сохранить перевод</button></div>
        </form>
      </div>
      <div class="card">
        <div class="card-header"><div><h3 class="card-title">Аналитика месяца</h3><p class="card-subtitle">${stats.transactionsThisMonth || 0} операций</p></div></div>
        <div class="list">
          ${expenseByCategory.slice(0, 5).map((item) => `
            <div class="list-item">
              <div class="list-main"><h4>${escapeHtml(item.category)}</h4><p>Расходы за месяц</p></div>
              ${financeAmount(item.amount, "expense")}
            </div>
          `).join("") || `<div class="empty">Расходов за месяц пока нет.</div>`}
        </div>
      </div>
    </section>

    <section class="card section-gap">
      <div class="card-header">
        <div><h3 class="card-title">Операции</h3><p class="card-subtitle">${filteredTransactions.length} из ${transactions.length} записей</p></div>
        <div class="top-actions">
          <select data-finance-filter="type">
            <option value="all" ${state.financeFilters.type === "all" ? "selected" : ""}>Все типы</option>
            <option value="income" ${state.financeFilters.type === "income" ? "selected" : ""}>Доходы</option>
            <option value="expense" ${state.financeFilters.type === "expense" ? "selected" : ""}>Расходы</option>
          </select>
          <select data-finance-filter="account">
            <option value="all" ${state.financeFilters.account === "all" ? "selected" : ""}>Все счета</option>
            ${accounts.map((account) => `<option value="${account.account_id}" ${Number(state.financeFilters.account) === Number(account.account_id) ? "selected" : ""}>${escapeHtml(account.account_name)}</option>`).join("")}
          </select>
          <input class="search" data-finance-filter="search" placeholder="Поиск" value="${escapeHtml(state.financeFilters.search)}" />
        </div>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Дата</th><th>Тип</th><th>Категория</th><th>Счет</th><th>Описание</th><th>Сумма</th><th></th></tr></thead>
          <tbody>
            ${filteredTransactions.map((item) => `
              <tr>
                <td>${dateRu(item.transaction_date)}</td>
                <td><span class="badge ${item.transaction_type === "income" ? "badge-green" : "badge-danger"}">${financeTypeLabel(item.transaction_type)}</span></td>
                <td>${escapeHtml(item.category_name)}</td>
                <td>${escapeHtml(item.account_name)}</td>
                <td>${escapeHtml(item.description || "")}</td>
                <td>${financeAmount(item.amount, item.transaction_type)}</td>
                <td><button class="icon-btn" title="Удалить" data-delete="/api/finance/transactions/${item.transaction_id}">×</button></td>
              </tr>
            `).join("") || `<tr><td colspan="7"><div class="empty">По фильтрам ничего не найдено.</div></td></tr>`}
          </tbody>
        </table>
      </div>
    </section>

    <section class="grid grid-2 section-gap">
      <div class="card">
        <div class="card-header"><div><h3 class="card-title">Переводы</h3><p class="card-subtitle">${transfers.length} записей</p></div></div>
        <div class="list">
          ${transfers.map((item) => `
            <div class="list-item">
              <div class="list-main"><h4>${escapeHtml(item.from_account_name)} -> ${escapeHtml(item.to_account_name)}</h4><p>${dateRu(item.transfer_date)} · ${escapeHtml(item.comment || "")}</p></div>
              <div class="row-actions">${financeAmount(item.amount, "transfer")}<button class="icon-btn" title="Удалить" data-delete="/api/finance/transfers/${item.transfer_id}">×</button></div>
            </div>
          `).join("") || `<div class="empty">Переводов пока нет.</div>`}
        </div>
      </div>
      <div class="card">
        <div class="card-header"><div><h3 class="card-title">Категории</h3><p class="card-subtitle">Доходы и расходы</p></div></div>
        <form data-endpoint="/api/finance/categories" data-method="POST" data-reload="finance">
          <div class="form-grid">
            <div class="field"><label>Название</label><input name="category_name" required /></div>
            <div class="field"><label>Тип</label><select name="category_type"><option value="expense">Расход</option><option value="income">Доход</option></select></div>
            <div class="field"><label>Иконка</label><input name="icon_name" placeholder="book" /></div>
            <div class="field"><label>Цвет</label><input name="color" type="color" value="#3c388d" /></div>
          </div>
          <div class="form-actions"><button class="btn btn-secondary" type="submit">Добавить категорию</button></div>
        </form>
        <div class="skills-wrap section-gap">${categories.map((category) => `<span class="skill-pill">${escapeHtml(category.category_name)} · ${category.category_type === "income" ? "доход" : "расход"}</span>`).join("")}</div>
      </div>
    </section>
  `;
}

function renderWorkouts(data) {
  const plans = data.plans || [];
  const exercises = data.exercises || [];
  const types = data.types || [];
  const planExercises = data.planExercises || [];
  const logs = data.logs || [];
  const logExercises = data.logExercises || [];
  const groups = data.groups || {};
  const stats = data.stats || {};
  const today = new Date().toISOString().slice(0, 10);
  const exercisesByPlan = planExercises.reduce((acc, item) => {
    (acc[item.plan_id] ||= []).push(item);
    return acc;
  }, {});
  const exercisesByLog = logExercises.reduce((acc, item) => {
    (acc[item.workout_log_id] ||= []).push(item);
    return acc;
  }, {});
  return `
    ${topbar("Тренировки", "План недели, упражнения и журнал прогресса.",
      `<button class="btn btn-secondary" data-action="reload">Обновить</button>`)}
    <section class="grid grid-4">
      <div class="kpi kpi-cyan"><h3>Планы</h3><strong>${plans.length}</strong><small>${stats.plannedDays || 0} дн. в недельной сетке</small></div>
      <div class="kpi kpi-green"><h3>Эта неделя</h3><strong>${stats.weeklyLogs || 0}</strong><small>${stats.weeklyMinutes || 0} мин · ${stats.weeklyCalories || 0} ккал</small></div>
      <div class="kpi kpi-primary"><h3>Сегодня</h3><strong>${stats.todayLogs || 0}</strong><small>${(groups.todayPlans || []).length} планов на день</small></div>
      <div class="kpi kpi-purple"><h3>Прогресс</h3><strong>${stats.completion || 0}%</strong><small>${stats.completedDays || 0} из ${stats.plannedDays || 0} плановых дней</small></div>
    </section>

    <section class="card section-gap">
      <div class="card-header"><div><h3 class="card-title">Ближайшая неделя</h3><p class="card-subtitle">Планы и отметки выполнения по дням.</p></div></div>
      <div class="calendar workout-calendar">
        ${(groups.weekDays || []).map((day) => {
          const dayLabel = scheduleDayLabel(day.date);
          return `
          <div class="day-cell ${day.isToday ? "day-current" : ""}">
            <div class="day-head">
              <strong>${escapeHtml(dayLabel.dateText)}</strong>
              <span>${escapeHtml(dayLabel.dayText)}</span>
            </div>
            ${(day.plans || []).map((plan) => `
              <div class="mini-panel workout-mini">
                <strong>${escapeHtml(plan.plan_name)}</strong>
                <span>${escapeHtml(plan.type_name || "")} · ${plan.planned_minutes || 0} мин</span>
              </div>
            `).join("")}
            ${(day.logs || []).map((log) => `<span class="badge badge-green">Выполнено · ${log.duration_minutes || 0} мин</span>`).join("")}
            ${!(day.plans || []).length && !(day.logs || []).length ? `<span class="muted">Свободный день</span>` : ""}
          </div>
        `}).join("")}
      </div>
    </section>

    <section class="grid grid-2 section-gap">
      <div class="card">
        <div class="card-header"><div><h3 class="card-title">Новый план</h3><p class="card-subtitle">День недели, тип и краткая цель.</p></div></div>
        <form data-endpoint="/api/workouts/plans" data-method="POST" data-reload="workouts">
          <div class="form-grid">
            <div class="field"><label>Название</label><input name="plan_name" required /></div>
            <div class="field"><label>Тип</label>${workoutTypeSelect(types, "workout_type_id")}</div>
            <div class="field"><label>День</label><select name="day_number">${dayShort.map((day, index) => `<option value="${index + 1}">${day}</option>`).join("")}</select></div>
            <div class="field"><label>Описание</label><input name="description" /></div>
          </div>
          <div class="form-actions"><button class="btn btn-cyan" type="submit">Добавить план</button></div>
        </form>
      </div>
      <div class="card">
        <div class="card-header"><div><h3 class="card-title">Упражнение в план</h3><p class="card-subtitle">Порядок, подходы и повторения.</p></div></div>
        <form data-endpoint="/api/workouts/plan-exercises" data-method="POST" data-reload="workouts">
          <div class="form-grid">
            <div class="field"><label>План</label>${planSelect(plans, "plan_id")}</div>
            <div class="field"><label>Упражнение</label>${exerciseSelect(exercises, "exercise_id")}</div>
            <div class="field"><label>Подходы</label><input name="sets_count" type="number" min="0" /></div>
            <div class="field"><label>Повторения</label><input name="reps_count" type="number" min="0" /></div>
            <div class="field"><label>Минуты</label><input name="duration_minutes" type="number" min="0" /></div>
            <div class="field"><label>Порядок</label><input name="exercise_order" type="number" min="1" /></div>
          </div>
          <div class="form-actions"><button class="btn btn-primary" type="submit" ${plans.length && exercises.length ? "" : "disabled"}>Добавить в план</button></div>
        </form>
      </div>
    </section>

    <section class="grid grid-2 section-gap">
      <div class="card">
        <div class="card-header"><div><h3 class="card-title">Журнал тренировки</h3><p class="card-subtitle">Запись факта выполнения.</p></div></div>
        <form data-endpoint="/api/workouts/logs" data-method="POST" data-reload="workouts">
          <div class="form-grid">
            <div class="field"><label>План</label>${planSelect(plans, "plan_id")}</div>
            <div class="field"><label>Дата</label><input name="workout_date" type="date" value="${today}" required /></div>
            <div class="field"><label>Минуты</label><input name="duration_minutes" type="number" min="0" /></div>
            <div class="field"><label>Ккал</label><input name="calories_burned" type="number" min="0" /></div>
            <div class="field field-full"><label>Заметки</label><input name="notes" /></div>
          </div>
          <div class="form-actions"><button class="btn btn-cyan" type="submit">Записать тренировку</button></div>
        </form>
      </div>
      <div class="card">
        <div class="card-header"><div><h3 class="card-title">Факт по упражнению</h3><p class="card-subtitle">Подходы, повторы, вес или время.</p></div></div>
        <form data-endpoint="/api/workouts/log-exercises" data-method="POST" data-reload="workouts">
          <div class="form-grid">
            <div class="field"><label>Запись журнала</label>${workoutLogSelect(logs, "workout_log_id")}</div>
            <div class="field"><label>Упражнение</label>${exerciseSelect(exercises, "exercise_id")}</div>
            <div class="field"><label>Подходы</label><input name="sets_done" type="number" min="0" /></div>
            <div class="field"><label>Повторения</label><input name="reps_done" type="number" min="0" /></div>
            <div class="field"><label>Вес, кг</label><input name="weight_used" type="number" min="0" step="0.5" /></div>
            <div class="field"><label>Минуты</label><input name="duration_minutes" type="number" min="0" /></div>
          </div>
          <div class="form-actions"><button class="btn btn-secondary" type="submit" ${logs.length && exercises.length ? "" : "disabled"}>Записать подходы</button></div>
        </form>
      </div>
    </section>

    <section class="grid grid-2 section-gap">
      <div class="card">
        <div class="card-header"><div><h3 class="card-title">Планы недели</h3><p class="card-subtitle">${plans.length} записей</p></div></div>
        <div class="list">
          ${plans.map((plan) => `
            <div class="list-item workout-plan-item">
              <div class="list-main">
                <h4>${dayNames[plan.day_number]} · ${escapeHtml(plan.plan_name)}</h4>
                <p>${escapeHtml(plan.type_name)} · ${plan.exercise_count || 0} упр. · ${plan.planned_minutes || 0} мин</p>
                ${plan.description ? `<p>${escapeHtml(plan.description)}</p>` : ""}
                <div class="skills-wrap">
                  ${(exercisesByPlan[plan.plan_id] || []).map((item) => `<span class="skill-pill">${escapeHtml(item.exercise_name)} · ${item.sets_count || 0}x${item.reps_count || 0}</span>`).join("")}
                </div>
              </div>
              <button class="icon-btn" title="Удалить" data-delete="/api/workouts/plans/${plan.plan_id}">×</button>
            </div>
          `).join("") || `<div class="empty">Планы не созданы.</div>`}
        </div>
      </div>
      <div class="card">
        <div class="card-header"><div><h3 class="card-title">Справочник упражнений</h3><p class="card-subtitle">${exercises.length} записей</p></div></div>
        <form data-endpoint="/api/workouts/exercises" data-method="POST" data-reload="workouts">
          <div class="form-grid">
            <div class="field"><label>Название</label><input name="exercise_name" required /></div>
            <div class="field"><label>Группа мышц</label><input name="muscle_group" /></div>
            <div class="field field-full"><label>Тип</label><input name="exercise_type" /></div>
          </div>
          <div class="form-actions"><button class="btn btn-secondary" type="submit">Добавить упражнение</button></div>
        </form>
        <div class="skills-wrap section-gap">
          ${exercises.map((exercise) => `<span class="skill-pill">${escapeHtml(exercise.exercise_name)}${exercise.muscle_group ? ` · ${escapeHtml(exercise.muscle_group)}` : ""}</span>`).join("")}
        </div>
      </div>
    </section>

    <section class="card section-gap">
      <div class="card-header"><div><h3 class="card-title">Журнал тренировок</h3><p class="card-subtitle">${logs.length} записей</p></div></div>
      <div class="list">
        ${logs.map((log) => `
          <div class="list-item">
            <div class="list-main">
              <h4>${dateRu(log.workout_date)} · ${escapeHtml(log.plan_name || "Без плана")}</h4>
              <p>${log.duration_minutes || 0} минут · ${log.calories_burned || 0} ккал · ${escapeHtml(log.notes || "")}</p>
              <div class="skills-wrap">
                ${(exercisesByLog[log.workout_log_id] || []).map((item) => `<span class="skill-pill">${escapeHtml(item.exercise_name)} · ${item.sets_done || 0}x${item.reps_done || 0}${item.weight_used ? ` · ${item.weight_used} кг` : ""}</span>`).join("")}
              </div>
            </div>
            <button class="icon-btn" title="Удалить" data-delete="/api/workouts/logs/${log.workout_log_id}">×</button>
          </div>
        `).join("") || `<div class="empty">В журнале пока пусто.</div>`}
      </div>
    </section>
  `;
}

function workoutTypeSelect(types = [], name) {
  return `<select name="${name}" ${types.length ? "" : "disabled"}>${types.map((type) => `<option value="${type.workout_type_id}">${escapeHtml(type.type_name)}</option>`).join("")}</select>`;
}

function planSelect(plans = [], name) {
  return `<select name="${name}" ${plans.length ? "" : "disabled"}>${plans.map((plan) => `<option value="${plan.plan_id}">${dayShort[(plan.day_number || 1) - 1]} · ${escapeHtml(plan.plan_name)}</option>`).join("")}</select>`;
}

function exerciseSelect(exercises = [], name) {
  return `<select name="${name}" ${exercises.length ? "" : "disabled"}>${exercises.map((exercise) => `<option value="${exercise.exercise_id}">${escapeHtml(exercise.exercise_name)}</option>`).join("")}</select>`;
}

function workoutLogSelect(logs = [], name) {
  return `<select name="${name}" ${logs.length ? "" : "disabled"}>${logs.map((log) => `<option value="${log.workout_log_id}">${dateRu(log.workout_date)} · ${escapeHtml(log.plan_name || "Без плана")}</option>`).join("")}</select>`;
}

function taskBadgeClass(task) {
  if (task.status === "done") return "badge-green";
  if (task.priority === "high") return "badge-purple";
  if (task.status === "in_progress") return "badge-cyan";
  return "badge-primary";
}

function filteredPlannerTasks(tasks = []) {
  const filters = state.plannerFilters;
  return tasks.filter((task) => {
    const statusOk = filters.status === "all"
      || (filters.status === "active" && task.status !== "done")
      || task.status === filters.status;
    const priorityOk = filters.priority === "all" || task.priority === filters.priority;
    return statusOk && priorityOk;
  });
}

function renderTaskItem(task) {
  const nextStatus = task.status === "done" ? "planned" : "done";
  return `
    <div class="list-item task-item ${task.status === "done" ? "task-done" : ""}">
      <div class="list-main">
        <h4>${escapeHtml(task.title)}</h4>
        <p>${escapeHtml(task.category_name || "Без категории")} · ${dateRu(task.due_date) || "без срока"} · ${statusLabel(task.status)}</p>
      </div>
      <div class="row-actions">
        <span class="badge ${taskBadgeClass(task)}">${priorityLabel(task.priority)}</span>
        ${task.status !== "done" ? `
          <form data-endpoint="/api/planner/tasks/${task.task_id}" data-method="PATCH" data-reload="planner">
            <input type="hidden" name="status" value="${nextStatus}" />
            <button class="btn btn-secondary" type="submit">Готово</button>
          </form>
        ` : `
          <form data-endpoint="/api/planner/tasks/${task.task_id}" data-method="PATCH" data-reload="planner">
            <input type="hidden" name="status" value="${nextStatus}" />
            <button class="btn btn-secondary" type="submit">Вернуть</button>
          </form>
        `}
        <button class="icon-btn" data-delete="/api/planner/tasks/${task.task_id}">×</button>
      </div>
    </div>
  `;
}

function renderPlanner(data) {
  const categories = data.categories || [];
  const tasks = data.tasks || [];
  const events = data.events || [];
  const notes = data.notes || [];
  const stats = data.stats || {};
  const groups = data.groups || {};
  const filteredTasks = filteredPlannerTasks(tasks);
  return `
    ${topbar("Ежедневник", "Задачи, события календаря и заметки.",
      `<button class="btn btn-secondary" data-action="reload">Обновить</button><a class="btn btn-primary" href="#planner-add">Добавить</a>`)}
    <section class="grid grid-4">
      <div class="kpi kpi-purple"><h3>Активные задачи</h3><strong>${stats.activeTasks || 0}</strong><small>${stats.highPriority || 0} высокий приоритет</small></div>
      <div class="kpi kpi-primary"><h3>Сегодня</h3><strong>${stats.todayTasks || 0}</strong><small>${stats.todayEvents || 0} событий</small></div>
      <div class="kpi kpi-cyan"><h3>События недели</h3><strong>${stats.weekEvents || 0}</strong><small>Ближайшие 7 дней</small></div>
      <div class="kpi kpi-green"><h3>Выполнено</h3><strong>${stats.doneTasks || 0}</strong><small>Заметок: ${stats.notes || 0}</small></div>
    </section>

    <section class="grid grid-2 section-gap">
      <div class="card">
        <div class="card-header"><div><h3 class="card-title">Сегодня и просрочено</h3><p class="card-subtitle">${stats.overdueTasks || 0} просрочено</p></div></div>
        <div class="list">
          ${(groups.overdueTasks || []).map(renderTaskItem).join("")}
          ${(groups.todayTasks || []).map(renderTaskItem).join("")}
          ${!(groups.overdueTasks || []).length && !(groups.todayTasks || []).length ? `<div class="empty">На сегодня срочных задач нет.</div>` : ""}
        </div>
      </div>
      <div class="card">
        <div class="card-header"><div><h3 class="card-title">Календарь недели</h3><p class="card-subtitle">${events.length} событий всего</p></div></div>
        <div class="calendar">
          ${(groups.calendarDays || []).map((day, index) => `
            <div class="day-cell ${index === 0 ? "day-current" : ""}">
              <strong>${dateRu(day.date).slice(0, 5)}</strong>
              <span class="muted">${dayShort[new Date(day.date).getDay() ? new Date(day.date).getDay() - 1 : 6]}</span>
              ${(day.events || []).map((event) => `<span class="badge badge-primary">${escapeHtml(event.start_time || "")} ${escapeHtml(event.title)}</span>`).join("")}
              ${(day.tasks || []).map((task) => `<span class="badge ${taskBadgeClass(task)}">${escapeHtml(task.title)}</span>`).join("")}
            </div>
          `).join("")}
        </div>
      </div>
    </section>

    <section class="grid grid-3 section-gap" id="planner-add">
      <div class="card">
        <div class="card-header"><div><h3 class="card-title">Новая задача</h3></div></div>
        <form data-endpoint="/api/planner/tasks" data-method="POST" data-reload="planner">
          <div class="form-grid">
            <div class="field field-full"><label>Название</label><input name="title" required /></div>
            <div class="field"><label>Категория</label>${plannerCategorySelect(categories, "planner_category_id")}</div>
            <div class="field"><label>Приоритет</label><select name="priority"><option value="medium">Средний</option><option value="high">Высокий</option><option value="low">Низкий</option></select></div>
            <div class="field"><label>Статус</label><select name="status"><option value="planned">Запланировано</option><option value="in_progress">В работе</option><option value="done">Выполнено</option></select></div>
            <div class="field"><label>Срок</label><input name="due_date" type="date" /></div>
            <div class="field field-full"><label>Описание</label><textarea name="description"></textarea></div>
          </div>
          <div class="form-actions"><button class="btn btn-purple" type="submit">Добавить задачу</button></div>
        </form>
      </div>
      <div class="card">
        <div class="card-header"><div><h3 class="card-title">Новое событие</h3></div></div>
        <form data-endpoint="/api/planner/events" data-method="POST" data-reload="planner">
          <div class="form-grid">
            <div class="field field-full"><label>Название</label><input name="title" required /></div>
            <div class="field"><label>Категория</label>${plannerCategorySelect(categories, "planner_category_id")}</div>
            <div class="field"><label>Дата</label><input name="event_date" type="date" required /></div>
            <div class="field"><label>Начало</label><input name="start_time" type="time" /></div>
            <div class="field"><label>Окончание</label><input name="end_time" type="time" /></div>
            <div class="field"><label>Место</label><input name="location" /></div>
            <div class="field field-full"><label>Описание</label><textarea name="description"></textarea></div>
          </div>
          <div class="form-actions"><button class="btn btn-primary" type="submit">Добавить событие</button></div>
        </form>
      </div>
      <div class="card">
        <div class="card-header"><div><h3 class="card-title">Новая заметка</h3></div></div>
        <form data-endpoint="/api/planner/notes" data-method="POST" data-reload="planner">
          <div class="form-grid">
            <div class="field field-full"><label>Заголовок</label><input name="title" required /></div>
            <div class="field field-full"><label>Текст</label><textarea name="content"></textarea></div>
          </div>
          <div class="form-actions"><button class="btn btn-secondary" type="submit">Добавить заметку</button></div>
        </form>
      </div>
    </section>

    <section class="grid grid-2 section-gap">
      <div class="card">
        <div class="card-header">
          <div><h3 class="card-title">Задачи</h3><p class="card-subtitle">${filteredTasks.length} из ${tasks.length} записей</p></div>
          <div class="top-actions">
            <select data-planner-filter="status">
              <option value="active" ${state.plannerFilters.status === "active" ? "selected" : ""}>Активные</option>
              <option value="all" ${state.plannerFilters.status === "all" ? "selected" : ""}>Все</option>
              <option value="planned" ${state.plannerFilters.status === "planned" ? "selected" : ""}>Запланировано</option>
              <option value="in_progress" ${state.plannerFilters.status === "in_progress" ? "selected" : ""}>В работе</option>
              <option value="done" ${state.plannerFilters.status === "done" ? "selected" : ""}>Выполнено</option>
            </select>
            <select data-planner-filter="priority">
              <option value="all" ${state.plannerFilters.priority === "all" ? "selected" : ""}>Все приоритеты</option>
              <option value="high" ${state.plannerFilters.priority === "high" ? "selected" : ""}>Высокий</option>
              <option value="medium" ${state.plannerFilters.priority === "medium" ? "selected" : ""}>Средний</option>
              <option value="low" ${state.plannerFilters.priority === "low" ? "selected" : ""}>Низкий</option>
            </select>
          </div>
        </div>
        <div class="list">${filteredTasks.map(renderTaskItem).join("") || `<div class="empty">Задачи не найдены.</div>`}</div>
      </div>
      <div class="card">
        <div class="card-header"><div><h3 class="card-title">События недели</h3><p class="card-subtitle">${(groups.weekEvents || []).length} ближайших</p></div></div>
        <div class="list">
          ${(groups.weekEvents || []).map((event) => `
            <div class="list-item">
              <div class="list-main"><h4>${escapeHtml(event.title)}</h4><p>${dateRu(event.event_date)} · ${escapeHtml([event.start_time, event.end_time].filter(Boolean).join("-"))} · ${escapeHtml(event.location || "место не указано")}</p></div>
              <button class="icon-btn" data-delete="/api/planner/events/${event.event_id}">×</button>
            </div>
          `).join("") || `<div class="empty">На неделю событий нет.</div>`}
        </div>
      </div>
    </section>

    <section class="grid grid-2 section-gap">
      <div class="card">
        <div class="card-header"><div><h3 class="card-title">Заметки</h3><p class="card-subtitle">${notes.length} записей</p></div></div>
        <div class="grid grid-2">
          ${notes.map((note) => `
            <div class="stat-box note-card">
              <div class="card-header"><strong>${escapeHtml(note.title)}</strong><button class="icon-btn" data-delete="/api/planner/notes/${note.note_id}">×</button></div>
              <div class="muted">${escapeHtml(note.content || "")}</div>
            </div>
          `).join("") || `<div class="empty">Заметок пока нет.</div>`}
        </div>
      </div>
      <div class="card">
        <div class="card-header"><div><h3 class="card-title">Категории</h3><p class="card-subtitle">${categories.length} записей</p></div></div>
        <form data-endpoint="/api/planner/categories" data-method="POST" data-reload="planner">
          <div class="form-grid">
            <div class="field field-full"><label>Название</label><input name="category_name" required /></div>
          </div>
          <div class="form-actions"><button class="btn btn-secondary" type="submit">Добавить категорию</button></div>
        </form>
        <div class="skills-wrap section-gap">${categories.map((category) => `<span class="skill-pill">${escapeHtml(category.category_name)}</span>`).join("")}</div>
      </div>
    </section>
  `;
}

function plannerCategorySelect(categories = [], name) {
  return `
    <select name="${name}" ${categories.length ? "" : "disabled"}>
      ${categories.length ? "" : `<option value="">Нет категорий</option>`}
      ${categories.map((category) => `<option value="${category.planner_category_id}">${escapeHtml(category.category_name)}</option>`).join("")}
    </select>
  `;
}

function renderPortfolio(data) {
  const categories = data.categories || [];
  const skills = data.skills || [];
  const projects = data.projects || [];
  const achievements = data.achievements || [];
  const certificates = data.certificates || [];
  const files = data.files || [];
  const stats = data.stats || {};
  const profile = state.profile || {};
  return `
    ${topbar("Портфолио", "Проекты, навыки, достижения, сертификаты и файлы.",
      `<button class="btn btn-secondary" data-action="reload">Обновить</button>`)}
    <section class="grid grid-4">
      <div class="kpi kpi-primary"><h3>Проекты</h3><strong>${stats.projects || 0}</strong><small>Учебные и личные</small></div>
      <div class="kpi kpi-purple"><h3>Сертификаты</h3><strong>${stats.certificates || 0}</strong><small>Подтверждения обучения</small></div>
      <div class="kpi kpi-cyan"><h3>Навыки</h3><strong>${stats.skills || 0}</strong><small>Компетенции</small></div>
      <div class="kpi kpi-green"><h3>Заполненность</h3><strong>${stats.completion || 0}%</strong><small>Профиль студента</small></div>
    </section>

    <section class="grid grid-2 section-gap">
      <div class="card">
        <div class="profile">
          <div class="avatar">${escapeHtml(initials(profile.full_name || state.user?.full_name))}</div>
          <div><h3 style="margin:0;">${escapeHtml(profile.full_name || state.user?.full_name)}</h3><div class="muted">${escapeHtml(profile.specialization || "Студент")} · ${escapeHtml(profile.group_name || "")}</div></div>
        </div>
        <p class="muted">${escapeHtml(profile.bio || "")}</p>
        <div class="skills-wrap">${skills.slice(0, 10).map((skill) => `<span class="skill-pill">${escapeHtml(skill.skill_name)}</span>`).join("")}</div>
      </div>
      <div class="card">
        <div class="card-header"><div><h3 class="card-title">Навыки</h3><p class="card-subtitle">${skills.length} записей</p></div></div>
        ${skills.slice(0, 6).map((skill) => `
          <div class="progress-row">
            <span>${escapeHtml(skill.skill_name)}</span>
            <div class="progress"><div class="progress-fill" style="width:${Number(skill.level || 0)}%"></div></div>
            <strong>${skill.level}%</strong>
          </div>
        `).join("")}
        <form class="section-gap" data-endpoint="/api/portfolio/skills" data-method="POST" data-reload="portfolio">
          <div class="form-grid">
            <div class="field"><label>Навык</label><input name="skill_name" required /></div>
            <div class="field"><label>Уровень</label><input name="level" type="number" min="0" max="100" value="70" /></div>
            <div class="field field-full"><label>Категория</label><input name="category" value="Общее" /></div>
          </div>
          <div class="form-actions"><button class="btn btn-secondary" type="submit">Добавить навык</button></div>
        </form>
      </div>
    </section>

    <section class="card section-gap">
      <div class="card-header"><div><h3 class="card-title">Проекты</h3><p class="card-subtitle">${projects.length} записей</p></div></div>
      <form data-endpoint="/api/portfolio/projects" data-method="POST" data-reload="portfolio">
        <div class="form-grid">
          <div class="field"><label>Название</label><input name="title" required /></div>
          <div class="field"><label>Категория</label>${portfolioCategorySelect(categories, "portfolio_category_id")}</div>
          <div class="field"><label>Тип</label><select name="project_type"><option value="учебный">Учебный</option><option value="личный">Личный</option><option value="дипломный">Дипломный</option><option value="практика">Практика</option></select></div>
          <div class="field"><label>Статус</label><select name="status"><option value="planned">Планируется</option><option value="in_progress">В разработке</option><option value="completed">Завершен</option></select></div>
          <div class="field"><label>Технологии</label><input name="technologies" /></div>
          <div class="field"><label>Результат</label><input name="result_text" /></div>
          <div class="field"><label>Начало</label><input name="start_date" type="date" /></div>
          <div class="field"><label>Окончание</label><input name="end_date" type="date" /></div>
          <div class="field"><label>Репозиторий</label><input name="repository_url" /></div>
          <div class="field"><label>Сайт</label><input name="project_url" /></div>
          <div class="field field-full"><label>Описание</label><textarea name="description"></textarea></div>
        </div>
        <div class="form-actions"><button class="btn btn-purple" type="submit">Добавить проект</button></div>
      </form>
      <div class="table-wrap section-gap">
        <table><thead><tr><th>Название</th><th>Тип</th><th>Технологии</th><th>Статус</th><th>Результат</th><th></th></tr></thead><tbody>
          ${projects.map((project) => `
            <tr><td>${escapeHtml(project.title)}</td><td>${escapeHtml(project.project_type)}</td><td>${escapeHtml(project.technologies || "")}</td><td>${statusLabel(project.status)}</td><td>${escapeHtml(project.result_text || "")}</td><td><button class="icon-btn" data-delete="/api/portfolio/projects/${project.project_id}">×</button></td></tr>
          `).join("")}
        </tbody></table>
      </div>
    </section>

    <section class="grid grid-2 section-gap">
      <div class="card">
        <div class="card-header"><div><h3 class="card-title">Достижение</h3></div></div>
        <form data-endpoint="/api/portfolio/achievements" data-method="POST" data-reload="portfolio">
          <div class="form-grid">
            <div class="field"><label>Название</label><input name="title" required /></div>
            <div class="field"><label>Категория</label>${portfolioCategorySelect(categories, "portfolio_category_id")}</div>
            <div class="field"><label>Тип</label><select name="achievement_type"><option value="достижение">Достижение</option><option value="награда">Награда</option><option value="грамота">Грамота</option><option value="участие">Участие</option></select></div>
            <div class="field"><label>Дата</label><input name="achievement_date" type="date" /></div>
            <div class="field"><label>Организация</label><input name="issuer" /></div>
            <div class="field"><label>Описание</label><input name="description" /></div>
          </div>
          <div class="form-actions"><button class="btn btn-primary" type="submit">Добавить достижение</button></div>
        </form>
        <div class="list section-gap">${achievements.map((item) => `<div class="list-item"><div class="list-main"><h4>${escapeHtml(item.title)}</h4><p>${escapeHtml(item.issuer || "")} · ${dateRu(item.achievement_date)}</p></div><button class="icon-btn" data-delete="/api/portfolio/achievements/${item.achievement_id}">×</button></div>`).join("")}</div>
      </div>
      <div class="card">
        <div class="card-header"><div><h3 class="card-title">Сертификат</h3></div></div>
        <form data-endpoint="/api/portfolio/certificates" data-method="POST" data-reload="portfolio">
          <div class="form-grid">
            <div class="field"><label>Название</label><input name="title" required /></div>
            <div class="field"><label>Категория</label>${portfolioCategorySelect(categories, "portfolio_category_id")}</div>
            <div class="field"><label>Организация</label><input name="organization" /></div>
            <div class="field"><label>Дата выдачи</label><input name="issue_date" type="date" /></div>
            <div class="field"><label>Номер</label><input name="certificate_number" /></div>
            <div class="field"><label>Файл</label><input name="file_path" /></div>
            <div class="field field-full"><label>Описание</label><textarea name="description"></textarea></div>
          </div>
          <div class="form-actions"><button class="btn btn-green" type="submit">Добавить сертификат</button></div>
        </form>
        <div class="list section-gap">${certificates.map((item) => `<div class="list-item"><div class="list-main"><h4>${escapeHtml(item.title)}</h4><p>${escapeHtml(item.organization || "")} · ${dateRu(item.issue_date)}</p></div><button class="icon-btn" data-delete="/api/portfolio/certificates/${item.certificate_id}">×</button></div>`).join("")}</div>
      </div>
    </section>

    <section class="card section-gap">
      <div class="card-header"><div><h3 class="card-title">Файлы портфолио</h3><p class="card-subtitle">${files.length} записей</p></div></div>
      <div class="list">${files.map((file) => `<div class="list-item"><div class="list-main"><h4>${escapeHtml(file.file_name)}</h4><p>${escapeHtml(file.file_type || "")} · ${escapeHtml(file.file_path)}</p></div><button class="icon-btn" data-delete="/api/portfolio/files/${file.file_id}">×</button></div>`).join("") || `<div class="empty">Файлы можно добавить через запись проекта, достижения или сертификата.</div>`}</div>
    </section>
  `;
}

function portfolioCategorySelect(categories = [], name) {
  return `<select name="${name}">${categories.map((category) => `<option value="${category.portfolio_category_id}">${escapeHtml(category.category_name)}</option>`).join("")}</select>`;
}

function lessonTypeClass(type) {
  const normalized = String(type || "").toLowerCase();
  if (normalized.includes("лек")) return "lesson-lecture";
  if (normalized.includes("лаб")) return "lesson-lab";
  if (normalized.includes("прак")) return "lesson-practice";
  if (normalized.includes("зач")) return "lesson-credit";
  if (normalized.includes("экз")) return "lesson-exam";
  return "lesson-default";
}

function renderScheduleGrid(lessons = [], week = {}) {
  const slots = [
    ["08:50", "10:25"],
    ["10:40", "12:15"],
    ["13:15", "14:50"],
    ["15:00", "16:35"],
    ["16:45", "18:20"],
    ["18:30", "20:05"],
    ["20:15", "21:50"]
  ];
  const byCell = new Map();
  for (const lesson of lessons) {
    const key = `${lesson.lesson_number}:${lesson.day_number}`;
    if (!byCell.has(key)) byCell.set(key, []);
    byCell.get(key).push(lesson);
  }
  const weekStart = scheduleWeekStart(week);
  const dayHeaders = [1, 2, 3, 4, 5, 6].map((dayNumber, index) => {
    const label = scheduleDayLabel(addDaysIso(weekStart, index));
    return { dayNumber, ...label };
  });

  return `
    <div class="schedule-board">
      <div class="schedule-head schedule-time-head"></div>
      ${dayHeaders.map((day) => `
        <div class="schedule-head">
          <strong>${escapeHtml(day.dateText)}</strong>
          <span>${escapeHtml(day.dayText)}</span>
        </div>
      `).join("")}
      ${slots.map(([start, end], index) => {
        const lessonNumber = index + 1;
        return `
          <div class="schedule-time"><strong>${start}</strong><span>${end}</span></div>
          ${[1, 2, 3, 4, 5, 6].map((dayNumber) => {
            const cellLessons = byCell.get(`${lessonNumber}:${dayNumber}`) || [];
            return `
              <div class="schedule-cell ${dayNumber === 2 ? "schedule-cell-muted" : ""}">
                ${cellLessons.map((lesson) => `
                  <article class="lesson-card ${lessonTypeClass(lesson.lesson_type)}">
                    <h4 title="${escapeHtml(lesson.discipline)}">${escapeHtml(lesson.discipline)}</h4>
                    <div>${escapeHtml(lesson.lesson_type || "")}</div>
                    <span>${escapeHtml(lesson.auditorium || "ауд. не указана")}</span>
                    <small>${escapeHtml(lesson.teacher_name || "")}</small>
                  </article>
                `).join("")}
              </div>
            `;
          }).join("")}
        `;
      }).join("")}
    </div>
    <div class="schedule-legend">
      <span class="legend-item lesson-lecture">Лекция</span>
      <span class="legend-item lesson-practice">Практика</span>
      <span class="legend-item lesson-lab">Лабораторная</span>
      <span class="legend-item lesson-credit">Зачет</span>
      <span class="legend-item lesson-exam">Экзамен</span>
    </div>
  `;
}

function renderSchedule(data) {
  const group = data.group || {};
  const week = data.week || {};
  const lessons = data.lessons || [];
  const navigation = data.navigation || {};
  const message = data.message || "Для выбранной группы расписание еще не загружено администратором.";
  const weekStart = scheduleWeekStart(week);
  const monthLabel = scheduleMonthLabel(weekStart);
  const weekRange = scheduleWeekRangeLabel(weekStart);
  const selectedWeek = navigation.selected || weekStart;
  const actions = `
    <button class="btn btn-secondary" type="button" data-schedule-week="${escapeHtml(navigation.previous || addDaysIso(selectedWeek, -7))}">← Неделя</button>
    <button class="btn ${selectedWeek === navigation.current ? "btn-primary" : "btn-secondary"}" type="button" data-schedule-week="${escapeHtml(navigation.current || currentWeekMondayIso())}">Текущая</button>
    <button class="btn btn-secondary" type="button" data-schedule-week="${escapeHtml(navigation.next || addDaysIso(selectedWeek, 7))}">Неделя →</button>
  `;
  return `
    ${topbar("Расписание", "Расписание выбранной группы. Обновление выполняет администратор.",
      `<span class="badge badge-primary">${escapeHtml(group.group_name || "Группа не выбрана")}</span>${actions}`)}
    <section class="grid grid-3">
      <div class="card"><h3 class="section-title">Группа</h3><div class="profile section-gap"><div class="avatar">${escapeHtml(initials(group.group_name || "ИС"))}</div><div><strong>${escapeHtml(group.group_name || "Не выбрана")}</strong><div class="muted">${escapeHtml(group.faculty_name || "Выберите группу в профиле")} · ${group.course_number || "-"} курс</div></div></div></div>
      <div class="card"><h3 class="section-title">${escapeHtml(monthLabel || "Неделя")}</h3><div class="section-gap"><span class="badge badge-primary">${weekLabel(week.week_type)}</span></div><div class="stat-note">${escapeHtml(weekRange || "Период не указан")}</div></div>
      <div class="card"><h3 class="section-title">Занятий</h3><div class="stat-value section-gap">${lessons.length}</div><div class="stat-note">Сохранено для выбранной группы</div></div>
    </section>
    <section class="card section-gap">
      <div class="card-header"><div><h3 class="card-title">Недельная сетка</h3><p class="card-subtitle">${escapeHtml(weekRange || monthLabel)} · пары распределены по дням и времени.</p></div></div>
      ${lessons.length ? renderScheduleGrid(lessons, week) : `<div class="empty">${escapeHtml(message)}</div>`}
    </section>
  `;
}

function facultyIdSelect(faculties = [], name, selected) {
  return `
    <select name="${name}" data-profile-faculty>
      ${faculties.length ? "" : `<option value="">Сначала загрузите группы в админке</option>`}
      ${faculties.map((faculty) => `<option value="${faculty.faculty_id}" ${Number(faculty.faculty_id) === Number(selected) ? "selected" : ""}>${escapeHtml(faculty.full_name)}</option>`).join("")}
    </select>
  `;
}

function courseNumberSelect(courses = [], name, selected) {
  return `
    <select name="${name}" data-profile-course>
      ${courses.length ? "" : `<option value="">Нет курсов</option>`}
      ${courses.map((course) => `<option value="${course.course_number}" ${Number(course.course_number) === Number(selected) ? "selected" : ""}>${course.course_number} курс</option>`).join("")}
    </select>
  `;
}

function profileGroupSelect(groups = [], selected) {
  return `
    <select name="group_id" data-profile-group ${groups.length ? "" : "disabled"}>
      ${groups.length ? "" : `<option value="">Нет групп для выбранного фильтра</option>`}
      ${groups.map((group) => `<option value="${group.group_id}" ${Number(group.group_id) === Number(selected) ? "selected" : ""}>${escapeHtml(group.group_name)}</option>`).join("")}
    </select>
  `;
}

function filteredProfileGroups(metadata, profile) {
  const groups = metadata.groups || [];
  const selectedGroup = groups.find((group) => Number(group.group_id) === Number(profile.group_id));
  const profileFacultyId = selectedGroup?.faculty_id || profile.faculty_id;
  const profileCourseNumber = selectedGroup?.course_number || profile.course_number;
  const filterFacultyId = state.profileFilters.facultyId;
  const filterCourseNumber = state.profileFilters.courseNumber;
  const facultyId = Number(filterFacultyId || profileFacultyId || groups[0]?.faculty_id || 0);
  const courseNumber = Number(filterCourseNumber || profileCourseNumber || groups.find((group) => Number(group.faculty_id) === facultyId)?.course_number || 1);
  return {
    facultyId,
    courseNumber,
    groups: groups.filter((group) => Number(group.faculty_id) === facultyId && Number(group.course_number) === courseNumber)
  };
}

function renderProfile(data) {
  const profile = data.profile || {};
  const metadata = data.metadata || {};
  const picker = filteredProfileGroups(metadata, profile);
  const selectedGroupId = picker.groups.some((group) => Number(group.group_id) === Number(profile.group_id))
    ? profile.group_id
    : picker.groups[0]?.group_id;
  const isComplete = Boolean(profile.full_name && profile.email && profile.group_id && profile.phone && profile.specialization);
  const educationText = profile.group_name
    ? `${profile.group_name} · ${profile.course_number || "-"} курс`
    : "Группа не выбрана";
  return `
    ${topbar("Профиль", "Выбор группы выполняется только здесь: факультет, курс, затем группа.",
      `<span class="badge ${isComplete ? "badge-green" : "badge-neutral"}">${isComplete ? "Заполнен" : "Нужно заполнить"}</span><button class="btn btn-secondary" data-action="reload">Обновить</button>`)}
    <section class="grid grid-2">
      <div class="card">
        <div class="profile">
          <div class="avatar">${escapeHtml(initials(profile.full_name || state.user?.full_name))}</div>
          <div>
            <h3 style="margin:0;">${escapeHtml(profile.full_name || state.user?.full_name)}</h3>
            <div class="muted">${escapeHtml(profile.email || state.user?.email)} · ${escapeHtml(educationText)}</div>
          </div>
        </div>
        <form class="section-gap" data-endpoint="/api/profile" data-method="PATCH" data-reload="profile" data-profile-form>
          <div class="form-grid">
            <div class="field"><label>ФИО</label><input name="full_name" value="${escapeHtml(profile.full_name || "")}" required /></div>
            <div class="field"><label>Email</label><input name="email" type="email" value="${escapeHtml(profile.email || "")}" required /></div>
            <div class="field"><label>Телефон</label><input name="phone" value="${escapeHtml(profile.phone || "")}" placeholder="+7 (900) 123-45-67" /></div>
            <div class="field"><label>Факультет</label>${facultyIdSelect(metadata.faculties, "faculty_picker", picker.facultyId)}</div>
            <div class="field"><label>Курс</label>${courseNumberSelect(metadata.courses, "course_picker", picker.courseNumber)}</div>
            <div class="field"><label>Группа</label>${profileGroupSelect(picker.groups, selectedGroupId)}</div>
            <div class="field field-full"><label>Направление</label><input name="specialization" value="${escapeHtml(profile.specialization || "")}" /></div>
            <div class="field field-full"><label>О себе</label><textarea name="bio">${escapeHtml(profile.bio || "")}</textarea></div>
          </div>
          <div class="form-actions"><button class="btn btn-primary" type="submit">Сохранить профиль</button></div>
        </form>
      </div>
      <div class="card">
        <h3 class="section-title">Обучение</h3>
        <div class="list section-gap">
          <div class="list-item"><div class="list-main"><h4>Факультет</h4><p>${escapeHtml(profile.faculty_name || "Не выбран")}</p></div></div>
          <div class="list-item"><div class="list-main"><h4>Курс</h4><p>${profile.course_number ? `${profile.course_number} курс` : "Не выбран"}</p></div></div>
          <div class="list-item"><div class="list-main"><h4>Группа</h4><p>${escapeHtml(profile.group_name || "Не выбрана")}</p></div></div>
          <div class="list-item"><div class="list-main"><h4>Специализация</h4><p>${escapeHtml(profile.specialization || "Не указана")}</p></div></div>
        </div>
        <div class="profile-hint section-gap">После сохранения выбранная группа используется в расписании и остальных модулях.</div>
      </div>
    </section>
  `;
}

function renderSettings(data) {
  const settings = data.settings || {};
  const profile = data.profile || {};
  const groupText = profile.group_name
    ? `${profile.group_name} · ${profile.course_number || "-"} курс`
    : "Группа не выбрана";
  const facultyText = profile.faculty_name || settings.faculty_name || "Факультет не выбран";
  return `
    ${topbar("Настройки", "Личные параметры приложения.",
      `<button class="btn btn-secondary" data-action="reload">Обновить</button>`)}
    <section class="grid grid-2">
      <div class="card">
        <h3 class="section-title">Предпочтения</h3>
        <form class="section-gap" data-endpoint="/api/settings" data-method="PATCH" data-reload="settings">
          <div class="form-grid">
            <div class="field"><label>Тема</label><select name="theme"><option value="light" ${settings.theme === "light" ? "selected" : ""}>Светлая</option><option value="dark" ${settings.theme === "dark" ? "selected" : ""}>Темная</option></select></div>
            <div class="field"><label>Уведомления</label><select name="notifications_enabled"><option value="1" ${settings.notifications_enabled ? "selected" : ""}>Включены</option><option value="0" ${!settings.notifications_enabled ? "selected" : ""}>Выключены</option></select></div>
            <div class="field field-full"><label>Напоминание перед парой, минут</label><input name="lesson_reminder_minutes" type="number" min="0" max="180" value="${settings.lesson_reminder_minutes ?? 15}" /></div>
          </div>
          <div class="form-actions"><button class="btn btn-primary" type="submit">Сохранить</button></div>
        </form>
      </div>
      <div class="card">
        <h3 class="section-title">Учебные данные</h3>
        <div class="mini-metrics section-gap">
          <div><span>Группа</span><strong>${escapeHtml(groupText)}</strong></div>
          <div><span>Факультет</span><strong>${escapeHtml(facultyText)}</strong></div>
        </div>
        <div class="list section-gap">
          <a class="list-item" href="#profile">
            <div class="list-main"><h4>Профиль</h4><p>Факультет, курс и группа студента</p></div>
            <span class="badge badge-cyan">Открыть</span>
          </a>
          <a class="list-item" href="#schedule">
            <div class="list-main"><h4>Расписание</h4><p>Актуальные пары выбранной группы</p></div>
            <span class="badge badge-green">Открыть</span>
          </a>
        </div>
      </div>
    </section>
  `;
}

function adminFilteredGroups(data) {
  const groups = data.groups || [];
  const faculty = state.adminFilters.faculty || "";
  const course = state.adminFilters.course || "";
  return groups.filter((group) => {
    const facultyOk = !faculty || group.faculty_code === faculty;
    const courseOk = !course || Number(group.course_number) === Number(course);
    return facultyOk && courseOk;
  });
}

function adminCourseOptions(groups = []) {
  return [...new Set(groups.map((group) => Number(group.course_number)).filter(Boolean))].sort((a, b) => a - b);
}

function adminGroupSelect(groups = [], selected) {
  return `
    <select name="group" ${groups.length ? "" : "disabled"}>
      ${groups.length ? "" : `<option value="">Нет групп для выбранного факультета</option>`}
      ${groups.map((group) => `<option value="${escapeHtml(group.group_name)}" ${group.group_name === selected ? "selected" : ""}>${escapeHtml(group.group_name)} · ${group.course_number} курс</option>`).join("")}
    </select>
  `;
}

function renderAdminGroupCoverageRows(groups = []) {
  if (!groups.length) {
    return `<tr><td colspan="6"><div class="empty">Группы по выбранному фильтру не найдены.</div></td></tr>`;
  }
  let currentFaculty = "";
  return groups.map((group) => {
    const facultyName = group.faculty_name || "Без факультета";
    const section = facultyName !== currentFaculty
      ? `<tr class="faculty-section-row"><td colspan="6">${escapeHtml(facultyName)}</td></tr>`
      : "";
    currentFaculty = facultyName;
    return `
      ${section}
      <tr>
        <td>${group.course_number}</td>
        <td><strong>${escapeHtml(group.group_name)}</strong></td>
        <td><span class="badge ${group.lesson_count ? "badge-green" : "badge-neutral"}">${group.lesson_count ? "Есть данные" : "Нет пар"}</span></td>
        <td>${group.lesson_count || 0}</td>
        <td>${escapeHtml(group.last_synced_at ? dateRu(group.last_synced_at) : "не было")}</td>
        <td>
          <form data-endpoint="/api/admin/schedule/sync" data-method="POST" data-reload="adminSchedule">
            <input type="hidden" name="faculty" value="${escapeHtml(group.faculty_code)}" />
            <input type="hidden" name="group" value="${escapeHtml(group.group_name)}" />
            <button class="btn btn-secondary" type="submit">Обновить</button>
          </form>
        </td>
      </tr>
    `;
  }).join("");
}

function renderAdminSchedule(data) {
  const faculties = data.faculties || [];
  const groups = data.groups || [];
  const filtered = adminFilteredGroups(data);
  const defaultFaculty = groups[0]?.faculty_code || faculties[0]?.site_code || faculties[0]?.abbreviation || "fvs";
  const groupsFaculty = state.adminActionFilters.groupsFaculty || defaultFaculty;
  const pointFaculty = state.adminActionFilters.pointFaculty || defaultFaculty;
  const massFaculty = state.adminActionFilters.massFaculty || "";
  const coverageFaculty = state.adminFilters.faculty || "";
  const courseOptions = adminCourseOptions(groups.filter((group) => !coverageFaculty || group.faculty_code === coverageFaculty));
  const currentCourse = state.adminFilters.course || "";
  const pointGroups = groups.filter((group) => group.faculty_code === pointFaculty);
  const selectedGroups = pointFaculty ? pointGroups : groups;
  const schedules = data.schedules || [];
  const weeks = data.weeks || [];
  const currentWeek = data.currentWeek || {};
  const summary = data.summary || {};
  const settings = data.syncSettings || {};
  const logs = data.syncLogs || [];
  const usersByGroup = data.usersByGroup || [];
  const usersByFaculty = data.usersByFaculty || [];
  return `
    ${topbar("Администрирование расписания", "Массовое обновление групп и расписаний, автообновление перед следующей неделей и контроль покрытия.",
      `<span class="badge badge-purple">Только администратор</span><span class="badge badge-cyan">ID текущей недели: ${escapeHtml(currentWeek.source_week_id || "не задан")}</span>`)}
    <section class="grid grid-4">
      <div class="kpi kpi-primary"><h3>Пользователи</h3><strong>${summary.student_users || 0}</strong><small>Студентов в системе</small></div>
      <div class="kpi kpi-cyan"><h3>Группы</h3><strong>${summary.total_groups || groups.length}</strong><small>${faculties.length} факультетов</small></div>
      <div class="kpi kpi-green"><h3>Покрытие</h3><strong>${summary.coverage_percent || 0}%</strong><small>${summary.groups_with_schedules || 0} групп с расписанием</small></div>
      <div class="kpi kpi-purple"><h3>Пар в базе</h3><strong>${groups.reduce((sum, group) => sum + Number(group.lesson_count || 0), 0)}</strong><small>По всем группам</small></div>
    </section>

    <section class="grid grid-3 section-gap">
      <div class="card">
        <div class="card-header"><div><h3 class="card-title">Группы</h3><p class="card-subtitle">Обновление справочников факультетов и групп.</p></div></div>
        <form data-endpoint="/api/admin/schedule/sync-groups" data-method="POST" data-reload="adminSchedule">
          <div class="form-grid">
            <div class="field field-full"><label>Факультет</label>${facultySelect(faculties, "faculty", groupsFaculty, "groupsFaculty")}</div>
          </div>
          <div class="form-actions"><button class="btn btn-secondary" type="submit">Обновить группы факультета</button></div>
        </form>
        <form class="section-gap" data-endpoint="/api/admin/schedule/sync-all-groups" data-method="POST" data-reload="adminSchedule">
          <button class="btn btn-cyan" type="submit">Обновить группы всех факультетов</button>
        </form>
      </div>
      <div class="card">
        <div class="card-header"><div><h3 class="card-title">Одна группа</h3><p class="card-subtitle">Точечное обновление расписания.</p></div></div>
        <form data-endpoint="/api/admin/schedule/sync" data-method="POST" data-reload="adminSchedule">
          <div class="form-grid">
            <div class="field"><label>Факультет</label>${facultySelect(faculties, "faculty", pointFaculty, "pointFaculty")}</div>
            <div class="field"><label>Группа</label>${adminGroupSelect(selectedGroups, selectedGroups[0]?.group_name)}</div>
            <div class="field field-full"><label>Неделя для парса</label><select name="week_offset"><option value="-1">Предыдущая</option><option value="0" selected>Текущая</option><option value="1">Следующая</option></select></div>
          </div>
          <div class="form-actions"><button class="btn btn-primary" type="submit" ${selectedGroups.length ? "" : "disabled"}>Обновить расписание группы</button></div>
        </form>
      </div>
      <div class="card">
        <div class="card-header"><div><h3 class="card-title">Массовое обновление</h3><p class="card-subtitle">Для всех групп или выбранного фильтра.</p></div></div>
        <form data-endpoint="/api/admin/schedule/sync-all" data-method="POST" data-reload="adminSchedule">
          <div class="form-grid">
            <div class="field"><label>Факультет</label>${facultySelectAll(faculties, "faculty", massFaculty, "massFaculty")}</div>
            <div class="field"><label>Неделя</label><select name="sync_mode"><option value="next">Следующая</option><option value="current">Текущая</option><option value="current_next">Текущая и следующая</option></select></div>
            <div class="field"><label>Лимит групп</label><input name="max_groups" type="number" min="1" placeholder="Без лимита" /></div>
            <div class="field"><label>Обновить группы</label><select name="refresh_groups"><option value="1">Да</option><option value="0">Нет</option></select></div>
          </div>
          <div class="form-actions"><button class="btn btn-green" type="submit">Запустить массово</button></div>
        </form>
      </div>
    </section>

    <section class="grid grid-2 section-gap">
      <div class="card">
        <div class="card-header"><div><h3 class="card-title">Автообновление перед неделей</h3><p class="card-subtitle">Фоновый запуск один раз перед ближайшим понедельником.</p></div></div>
        <form data-endpoint="/api/admin/schedule/settings" data-method="PATCH" data-reload="adminSchedule">
          <div class="form-grid">
            <div class="field"><label>Состояние</label><select name="enabled"><option value="1" ${settings.enabled ? "selected" : ""}>Включено</option><option value="0" ${!settings.enabled ? "selected" : ""}>Выключено</option></select></div>
            <div class="field"><label>За сколько дней</label><input name="lead_days" type="number" min="0" max="6" value="${settings.lead_days ?? 2}" /></div>
            <div class="field"><label>Время запуска</label><input name="run_time" type="time" value="${escapeHtml(settings.run_time || "18:00")}" /></div>
            <div class="field"><label>Что обновлять</label><select name="sync_mode"><option value="next" ${settings.sync_mode === "next" ? "selected" : ""}>Следующую неделю</option><option value="current" ${settings.sync_mode === "current" ? "selected" : ""}>Текущую неделю</option><option value="current_next" ${settings.sync_mode === "current_next" ? "selected" : ""}>Текущую и следующую</option></select></div>
          </div>
          <div class="form-actions"><button class="btn btn-primary" type="submit">Сохранить автообновление</button></div>
        </form>
        <form class="section-gap" data-endpoint="/api/admin/schedule/run-auto" data-method="POST" data-reload="adminSchedule">
          <button class="btn btn-secondary" type="submit">Запустить автообновление сейчас</button>
        </form>
      </div>
      <div class="card">
        <div class="card-header"><div><h3 class="card-title">Сводка пользователей</h3><p class="card-subtitle">Из каких групп и факультетов студенты.</p></div></div>
        <div class="list">
          ${usersByFaculty.map((item) => `<div class="list-item"><div class="list-main"><h4>${escapeHtml(item.faculty_name || "Без факультета")}</h4><p>${escapeHtml(item.faculty_code || "")}</p></div><span class="badge badge-primary">${item.user_count} студ.</span></div>`).join("") || `<div class="empty">Пока нет студентов с выбранной группой.</div>`}
        </div>
      </div>
    </section>

    <section class="card section-gap">
      <div class="card-header admin-coverage-header">
        <div><h3 class="card-title">Группы и покрытие расписанием</h3><p class="card-subtitle">Фильтр по факультету и курсу. Студенты выбирают группу из этих данных в профиле.</p></div>
        <div class="top-actions">
          <select data-admin-filter="faculty">
            <option value="" ${!state.adminFilters.faculty ? "selected" : ""}>Все факультеты</option>
            ${faculties.map((faculty) => `<option value="${escapeHtml(faculty.site_code || faculty.abbreviation)}" ${(faculty.site_code || faculty.abbreviation) === state.adminFilters.faculty ? "selected" : ""}>${escapeHtml(facultyShortName(faculty.full_name || faculty.abbreviation || faculty.site_code))}</option>`).join("")}
          </select>
          <select data-admin-filter="course">
            <option value="">Все курсы</option>
            ${courseOptions.map((course) => `<option value="${course}" ${String(course) === String(currentCourse) ? "selected" : ""}>${course} курс</option>`).join("")}
          </select>
        </div>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Курс</th><th>Группа</th><th>Статус</th><th>Пар</th><th>Синхронизация</th><th></th></tr></thead>
          <tbody>
            ${renderAdminGroupCoverageRows(filtered)}
          </tbody>
        </table>
      </div>
    </section>

    <section class="grid grid-2 section-gap">
      <div class="card">
        <div class="card-header"><div><h3 class="card-title">Недели расписания</h3><p class="card-subtitle">Контроль дат, номеров недель и объема синхронизации.</p></div></div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Неделя</th><th>Период</th><th>Тип</th><th>Групп</th><th>Пар</th></tr></thead>
            <tbody>
              ${weeks.map((week) => `
                <tr>
                  <td><strong>${week.week_number ? `№ ${week.week_number}` : "Без номера"}</strong></td>
                  <td>${dateRu(week.starts_at)} - ${dateRu(week.ends_at)}</td>
                  <td><span class="badge badge-neutral">${escapeHtml(week.week_type || "обычная")}</span></td>
                  <td>${week.group_count || 0}</td>
                  <td>${week.lesson_count || 0}</td>
                </tr>
              `).join("") || `<tr><td colspan="5"><div class="empty">Недели пока не сохранены.</div></td></tr>`}
            </tbody>
          </table>
        </div>
      </div>
      <div class="card">
        <div class="card-header"><div><h3 class="card-title">Последние расписания</h3><p class="card-subtitle">Недавно сохраненные группы и периоды.</p></div></div>
        <div class="list">
          ${schedules.slice(0, 8).map((schedule) => `<div class="list-item"><div class="list-main"><h4>${escapeHtml(schedule.group_name || "Группа")}</h4><p>${dateRu(schedule.starts_at)} - ${dateRu(schedule.ends_at)} · ${escapeHtml(schedule.faculty_name || "")} · пар ${schedule.lesson_count || 0}</p></div><span class="badge badge-cyan">${schedule.week_number ? `№ ${schedule.week_number}` : escapeHtml(schedule.week_type || "")}</span></div>`).join("") || `<div class="empty">Расписаний пока нет.</div>`}
        </div>
      </div>
    </section>

    <section class="grid grid-2 section-gap">
      <div class="card">
        <div class="card-header"><div><h3 class="card-title">Журнал синхронизации</h3><p class="card-subtitle">Последние ручные и автоматические запуски.</p></div></div>
        <div class="list">
          ${logs.map((log) => `<div class="list-item"><div class="list-main"><h4>${escapeHtml(syncTriggerLabel(log.trigger_type))} · ${escapeHtml(syncStatusLabel(log.status))}</h4><p>${dateRu(log.started_at)} · ${escapeHtml(log.target_scope || "")} · групп ${log.synced_groups}/${log.total_groups}, пар ${log.lesson_count}, пустых ${log.empty_groups}, ошибок ${log.error_count}</p></div><span class="badge ${log.status === "success" ? "badge-green" : "badge-purple"}">${escapeHtml(log.target_week)}</span></div>`).join("") || `<div class="empty">Запусков пока нет.</div>`}
        </div>
      </div>
      <div class="card">
        <div class="card-header"><div><h3 class="card-title">Группы студентов</h3><p class="card-subtitle">Куда распределены пользователи.</p></div></div>
        <div class="list">
          ${usersByGroup.map((item) => `<div class="list-item"><div class="list-main"><h4>${escapeHtml(item.group_name || "Без группы")}</h4><p>${escapeHtml(item.faculty_name || "")} · ${item.course_number || "-"} курс</p></div><span class="badge badge-cyan">${item.user_count} студ.</span></div>`).join("") || `<div class="empty">Пока нет выбранных групп.</div>`}
        </div>
      </div>
    </section>
  `;
}

function formPayload(form) {
  return Object.fromEntries(new FormData(form).entries());
}

function validateAuthPayload(form, payload) {
  if (form.dataset.auth === "register" && payload.password !== payload.password_confirm) {
    throw new Error("Пароли не совпадают");
  }
  if (payload.password && payload.password.length < 6) {
    throw new Error("Пароль должен быть не короче 6 символов");
  }
}

document.addEventListener("click", async (event) => {
  const authTab = event.target.closest("[data-auth-tab]");
  if (authTab) {
    state.authMode = authTab.dataset.authTab;
    state.notice = "";
    renderAuth();
    return;
  }

  const logout = event.target.closest("[data-logout]");
  if (logout) {
    await api("/api/auth/logout", { method: "POST" });
    state.user = null;
    state.profile = null;
    applyTheme("light");
    renderAuth();
    return;
  }

  const reload = event.target.closest('[data-action="reload"]');
  if (reload) {
    await loadRoute(state.route);
    notify("Данные обновлены");
    return;
  }

  const scheduleWeek = event.target.closest("[data-schedule-week]");
  if (scheduleWeek) {
    const value = scheduleWeek.dataset.scheduleWeek;
    if (value) {
      state.scheduleWeekStart = value;
      await loadRoute("schedule");
    }
    return;
  }

  const deleteButton = event.target.closest("[data-delete]");
  if (deleteButton) {
    const confirmed = confirm("Удалить запись?");
    if (!confirmed) return;
    try {
      const result = await api(deleteButton.dataset.delete, { method: "DELETE" });
      await loadRoute(state.route);
      notify(result?.message || "Запись удалена");
    } catch (error) {
      state.notice = error.message;
      renderRoute();
      notify(error.message, "error");
    }
  }
});

document.addEventListener("change", (event) => {
  const adminActionFilter = event.target.closest("[data-admin-action-filter]");
  if (adminActionFilter) {
    state.adminActionFilters[adminActionFilter.dataset.adminActionFilter] = adminActionFilter.value;
    renderRoute();
    return;
  }

  const adminFilter = event.target.closest("[data-admin-filter]");
  if (adminFilter) {
    state.adminFilters[adminFilter.dataset.adminFilter] = adminFilter.value;
    if (adminFilter.dataset.adminFilter === "faculty") {
      state.adminFilters.course = "";
    }
    renderRoute();
    return;
  }

  const financeFilter = event.target.closest("[data-finance-filter]");
  if (financeFilter) {
    state.financeFilters[financeFilter.dataset.financeFilter] = financeFilter.value;
    renderRoute();
    return;
  }

  const plannerFilter = event.target.closest("[data-planner-filter]");
  if (plannerFilter) {
    state.plannerFilters[plannerFilter.dataset.plannerFilter] = plannerFilter.value;
    renderRoute();
    return;
  }

  const profileFaculty = event.target.closest("[data-profile-faculty]");
  if (profileFaculty) {
    state.profileFilters.facultyId = profileFaculty.value;
    const groups = state.data?.metadata?.groups || [];
    const firstCourse = groups.find((group) => Number(group.faculty_id) === Number(profileFaculty.value))?.course_number || "";
    state.profileFilters.courseNumber = firstCourse;
    renderRoute();
    return;
  }

  const profileCourse = event.target.closest("[data-profile-course]");
  if (profileCourse) {
    state.profileFilters.courseNumber = profileCourse.value;
    renderRoute();
  }
});

document.addEventListener("submit", async (event) => {
  const authForm = event.target.closest("form[data-auth]");
  if (authForm) {
    event.preventDefault();
    try {
      const action = authForm.dataset.auth === "login" ? "/api/auth/login" : "/api/auth/register";
      const payload = formPayload(authForm);
      validateAuthPayload(authForm, payload);
      const result = await api(action, { method: "POST", body: JSON.stringify(payload) });
      state.user = result.user;
      state.profile = result.profile;
      state.notice = "";
      location.hash = state.user?.is_admin
        ? "#adminSchedule"
        : (authForm.dataset.auth === "register" ? "#profile" : "#dashboard");
      await loadRoute(routeFromHash());
      notify(result?.message || (authForm.dataset.auth === "login" ? "Вход выполнен" : "Аккаунт создан"));
    } catch (error) {
      state.notice = error.message;
      renderAuth();
    }
    return;
  }

  const form = event.target.closest("form[data-endpoint]");
  if (!form) return;
  event.preventDefault();
  const endpoint = form.dataset.endpoint;
  const method = form.dataset.method || "POST";
  const payload = formPayload(form);
  const syncProgress = syncProgressForEndpoint(endpoint);
  if (syncProgress) {
    payload._progress = "1";
    state.syncProgress = { ...syncProgress, status: "В процессе", processed: 0, total: 0, percent: 0 };
    renderRoute();
  }
  try {
    let result = await api(endpoint, {
      method,
      body: JSON.stringify(payload)
    });
    if (syncProgress && result?.job_id) {
      result = await pollSyncProgress(result.job_id, syncProgress);
    }
    if (method.toUpperCase() === "POST") {
      form.reset();
    }
    if (result?.user) state.user = result.user;
    if (result?.profile) state.profile = result.profile;
    state.syncProgress = null;
    await loadRoute(form.dataset.reload || state.route);
    if (result?.warning) {
      state.notice = result.warning;
      renderRoute();
      notify(result.warning, "warning");
    } else {
      notify(result?.message || "Действие выполнено");
    }
  } catch (error) {
    state.syncProgress = null;
    state.notice = error.message;
    renderRoute();
    notify(error.message, "error");
  }
});

window.addEventListener("hashchange", () => {
  if (!state.user) return;
  loadRoute(routeFromHash());
});

init();
