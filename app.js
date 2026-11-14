const navItems = document.querySelectorAll(".nav-item");
const pages = document.querySelectorAll(".page");
const title = document.getElementById("page-title");
const toast = document.getElementById("toast");

function showView(view) {
  pages.forEach((page) => page.classList.toggle("hidden", page.id !== `${view}-view`));
  navItems.forEach((item) => item.classList.toggle("active", item.dataset.view === view));
  const active = document.querySelector(`[data-view="${view}"]`);
  title.textContent = active ? active.textContent.replace(/[↔◒✓✦⚙◈]/g, "").replace(/\d+/g, "").trim() : "Overview";
  window.scrollTo({ top: 0, behavior: "smooth" });
}

navItems.forEach((item) => item.addEventListener("click", () => showView(item.dataset.view)));
document.querySelectorAll("[data-view-link]").forEach((button) => {
  button.addEventListener("click", () => {
    showView(button.dataset.viewLink);
    if (button.dataset.transactionFilter) {
      document.querySelector(`.tab[data-filter="${button.dataset.transactionFilter}"]`).click();
    }
  });
});

const importButton = document.getElementById("import-button");
const fileInput = document.getElementById("transaction-file-input");
importButton.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => {
  if (!fileInput.files.length) return;
  toast.textContent = `${fileInput.files[0].name} ready to import`;
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 2200);
});

document.querySelectorAll("[data-scroll-target]").forEach((button) => {
  button.addEventListener("click", () => {
    document.getElementById(button.dataset.scrollTarget).scrollIntoView({ behavior: "smooth", block: "start" });
  });
});

const profileMenuButton = document.getElementById("profile-menu-button");
const profileMenu = document.getElementById("profile-menu");
profileMenuButton.addEventListener("click", () => {
  const isOpen = !profileMenu.classList.contains("hidden");
  profileMenu.classList.toggle("hidden", isOpen);
  profileMenuButton.setAttribute("aria-expanded", String(!isOpen));
});

document.getElementById("sign-out-button").addEventListener("click", () => {
  profileMenu.classList.add("hidden");
  profileMenuButton.setAttribute("aria-expanded", "false");
  document.querySelector(".app-shell").classList.add("hidden");
  document.getElementById("login-view").classList.remove("hidden");
});

const accountProfiles = {
  "Nimbus Studio": {
    balance: "$84,290",
    balanceDecimal: ".42",
    connectedAccounts: "Across 2 connected accounts",
    spend: "$12,486",
    spendDecimal: ".18",
    previousSpend: "$12,752",
    deductible: "$9,218",
    deductibleDecimal: ".42",
    deductiblePercent: "73.8%",
    bank: "Nessie Bank",
    categorizedTransactions: 48,
    profileEmail: "alex@nimbusstudio.com",
  },
  "Cedar & Co.": {
    balance: "$41,860",
    balanceDecimal: ".77",
    connectedAccounts: "Across 1 connected account",
    spend: "$8,214",
    spendDecimal: ".56",
    previousSpend: "$9,031",
    deductible: "$6,102",
    deductibleDecimal: ".38",
    deductiblePercent: "74.3%",
    bank: "Mercury",
    categorizedTransactions: 31,
    profileEmail: "alex@cedarandco.com",
  },
};

let activeAccountName = "Nimbus Studio";
function setProfileSummary(profile, accountName, ownerName = "Alex Thompson") {
  const summary = document.getElementById("profile-summary");
  summary.replaceChildren(
    document.createTextNode(ownerName),
    document.createElement("br"),
    document.createTextNode(`Owner · ${accountName}`),
    document.createElement("br"),
    document.createTextNode(profile.profileEmail),
  );
}

function applyAccountProfile(accountName) {
  const profile = accountProfiles[accountName] || {
    ...accountProfiles["Cedar & Co."],
    profileEmail: `owner@${accountName.toLowerCase().replace(/[^a-z0-9]+/g, "")}.com`,
  };
  activeAccountName = accountName;
  document.getElementById("active-account-name").textContent = accountName;
  document.getElementById("workspace-avatar").textContent = accountName.charAt(0).toUpperCase();
  document.getElementById("overview-subheading").textContent = `Here’s how ${accountName} is doing this month.`;
  document.getElementById("transactions-subheading").textContent = `Review and categorize every ${accountName} purchase.`;
  document.getElementById("balance-value").innerHTML = `${profile.balance}<span class="decimal">${profile.balanceDecimal}</span>`;
  document.getElementById("balance-foot").innerHTML = `<span class="account-dot"></span> ${profile.connectedAccounts}`;
  document.getElementById("spend-value").innerHTML = `${profile.spend}<span class="decimal">${profile.spendDecimal}</span>`;
  document.getElementById("spend-foot").innerHTML = `<span class="muted">vs. ${profile.previousSpend} last month</span>`;
  document.getElementById("deductible-value").innerHTML = `${profile.deductible}<span class="decimal">${profile.deductibleDecimal}</span>`;
  document.getElementById("deductible-foot").innerHTML = `<span class="green-dot"></span> ${profile.deductiblePercent} of total spend`;
  document.getElementById("deductible-percent").textContent = profile.deductiblePercent;
  document.getElementById("tax-progress-percent").textContent = `${profile.deductiblePercent} categorized as deductible`;
  document.getElementById("sync-account-name").textContent = `Just now · ${profile.bank}`;
  document.getElementById("tax-deductible-total").innerHTML = `${profile.deductible}${profile.deductibleDecimal} <small>deductible spend</small>`;
  document.getElementById("tax-summary").textContent = `Based on ${profile.categorizedTransactions} categorized transactions through September 30.`;
  document.getElementById("profile-email").value = profile.profileEmail;
  setProfileSummary(profile, accountName);
  document.querySelector("#account-summary p").innerHTML = `${profile.bank}<br /><span style="color:var(--green)">● Synced just now</span>`;
  document.querySelector("#account-manager strong").textContent = `${profile.bank} · Business checking`;
}

const workspaceSwitcher = document.getElementById("workspace-switcher");
const workspaceMenu = document.getElementById("workspace-menu");
workspaceSwitcher.addEventListener("click", (event) => {
  if (event.target.closest("button") || event.target.closest("#workspace-account-form")) return;
  const isOpen = !workspaceMenu.classList.contains("hidden");
  workspaceMenu.classList.toggle("hidden", isOpen);
  workspaceSwitcher.setAttribute("aria-expanded", String(!isOpen));
});

document.querySelectorAll(".account-option").forEach((option) => {
  option.addEventListener("click", () => {
    applyAccountProfile(option.dataset.account);
    workspaceMenu.classList.add("hidden");
    workspaceSwitcher.setAttribute("aria-expanded", "false");
    toast.textContent = `Switched to ${option.dataset.account}`;
    toast.classList.add("show");
    setTimeout(() => toast.classList.remove("show"), 2200);
  });
});

document.getElementById("add-account-button").addEventListener("click", () => {
  document.getElementById("workspace-account-form").classList.remove("hidden");
  document.getElementById("new-account-name").focus();
});

document.getElementById("cancel-add-account").addEventListener("click", () => {
  document.getElementById("workspace-account-form").classList.add("hidden");
});

document.getElementById("workspace-account-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const accountName = document.getElementById("new-account-name").value.trim();
  applyAccountProfile(accountName);
  document.getElementById("workspace-account-form").classList.add("hidden");
  workspaceMenu.classList.add("hidden");
  workspaceSwitcher.setAttribute("aria-expanded", "false");
  toast.textContent = `${accountName} added`;
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 2200);
});

const searchPanel = document.getElementById("search-panel");
document.getElementById("search-button").addEventListener("click", () => {
  searchPanel.classList.toggle("hidden");
  if (!searchPanel.classList.contains("hidden")) document.getElementById("global-search").focus();
});

document.getElementById("notifications-button").addEventListener("click", () => {
  toast.textContent = "You’re all caught up";
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 2200);
});

document.getElementById("help-button").addEventListener("click", () => {
  document.getElementById("help-panel").classList.toggle("hidden");
});

document.getElementById("help-guide-button").addEventListener("click", () => {
  document.getElementById("help-content").classList.add("hidden");
  document.getElementById("guide-content").classList.remove("hidden");
});

document.getElementById("guide-back-button").addEventListener("click", () => {
  document.getElementById("guide-content").classList.add("hidden");
  document.getElementById("help-content").classList.remove("hidden");
});

const appShell = document.querySelector(".app-shell");
const loginView = document.getElementById("login-view");
document.getElementById("login-form").addEventListener("submit", (event) => {
  event.preventDefault();
  appShell.classList.remove("hidden");
  loginView.classList.add("hidden");
  showView("overview");
  toast.textContent = "Signed in successfully";
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 2200);
});

function applyTransactionFilter(filter) {
  document.querySelectorAll(".tab[data-filter]").forEach((item) => {
    const selected = item.dataset.filter === filter;
    item.classList.toggle("active", selected);
    item.setAttribute("aria-selected", selected);
  });
  document.querySelectorAll(".table-row[data-status]").forEach((row) => {
    row.classList.toggle("hidden", filter !== "all" && row.dataset.status !== filter);
  });
}

document.querySelectorAll(".tab[data-filter]").forEach((tab) => {
  tab.addEventListener("click", () => {
    applyTransactionFilter(tab.dataset.filter);
  });
});

const filterButton = document.getElementById("filter-button");
const filterMenu = document.getElementById("transaction-filter-menu");
filterButton.addEventListener("click", () => {
  const isOpen = !filterMenu.classList.contains("hidden");
  filterMenu.classList.toggle("hidden", isOpen);
  filterButton.setAttribute("aria-expanded", String(!isOpen));
});

document.getElementById("apply-filter").addEventListener("click", () => {
  applyTransactionFilter(document.getElementById("status-filter").value);
  filterMenu.classList.add("hidden");
  filterButton.setAttribute("aria-expanded", "false");
});

document.getElementById("clear-filter").addEventListener("click", () => {
  document.getElementById("status-filter").value = "all";
  applyTransactionFilter("all");
  filterMenu.classList.add("hidden");
  filterButton.setAttribute("aria-expanded", "false");
});

document.querySelectorAll(".small-button").forEach((button) => {
  button.addEventListener("click", () => {
    button.textContent = "Done";
    button.disabled = true;
    button.style.opacity = ".55";
    toast.textContent = "Item marked as reviewed";
    toast.classList.add("show");
    setTimeout(() => toast.classList.remove("show"), 2200);
  });
});

document.querySelectorAll(".suggestions button").forEach((button) => {
  button.addEventListener("click", () => {
    document.getElementById("chat-input").value = button.textContent;
    document.getElementById("chat-input").focus();
  });
});

document.getElementById("ask-submit").addEventListener("click", () => {
  const input = document.getElementById("chat-input");
  if (!input.value.trim()) {
    input.focus();
    return;
  }
  document.getElementById("chat-answer").classList.remove("hidden");
  input.value = "";
});

const dateButton = document.getElementById("date-button");
const customDateForm = document.getElementById("custom-date-form");

dateButton.addEventListener("change", (event) => {
  const isCustom = event.target.value === "Custom date range";
  customDateForm.classList.toggle("hidden", !isCustom);
  if (!isCustom) {
    toast.textContent = `Date range changed to ${event.target.value}`;
    toast.classList.add("show");
    setTimeout(() => toast.classList.remove("show"), 2200);
  }
});

document.getElementById("cancel-date-button").addEventListener("click", () => {
  customDateForm.classList.add("hidden");
  dateButton.value = "September 2024";
});

customDateForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const start = document.getElementById("date-start").value;
  const end = document.getElementById("date-end").value;
  if (start > end) {
    toast.textContent = "Start date must be before end date";
  } else {
    toast.textContent = `Custom range applied: ${start} to ${end}`;
    customDateForm.classList.add("hidden");
  }
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 2200);
});

document.getElementById("download-report").addEventListener("click", () => {
  const profile = accountProfiles[activeAccountName] || accountProfiles["Cedar & Co."];
  const report = [
    `Ledgerly Tax Readiness Report - ${activeAccountName}`,
    "Period,2024",
    "",
    "Category,Deductible percentage,Amount",
    "Supplies,100%,\"$4,928\"",
    "Software,100%,\"$1,806\"",
    "Client meals,50%,\"$1,206\"",
    "",
    `Total deductible spend,${profile.deductible}${profile.deductibleDecimal}`,
    `Categorized as deductible,${profile.deductiblePercent}`,
  ].join("\n");
  const blob = new Blob([report], { type: "text/csv;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "ledgerly-tax-readiness-2024.csv";
  link.click();
  URL.revokeObjectURL(link.href);
  toast.textContent = "Tax report downloaded";
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 2200);
});

const profileDetails = document.getElementById("profile-details");
const profileForm = document.getElementById("profile-form");
const profileName = document.getElementById("profile-name");
const profileEmail = document.getElementById("profile-email");

document.getElementById("edit-profile-button").addEventListener("click", () => {
  profileDetails.classList.add("hidden");
  profileForm.classList.remove("hidden");
  profileName.focus();
});

document.getElementById("cancel-profile-button").addEventListener("click", () => {
  profileForm.classList.add("hidden");
  profileDetails.classList.remove("hidden");
});

profileForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const name = profileName.value.trim();
  const email = profileEmail.value.trim();
  if (!name || !email) {
    toast.textContent = "Name and email are required";
    toast.classList.add("show");
    setTimeout(() => toast.classList.remove("show"), 2200);
    return;
  }
  setProfileSummary({ profileEmail: email }, activeAccountName, name);
  profileForm.classList.add("hidden");
  profileDetails.classList.remove("hidden");
  toast.textContent = "Profile updated";
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 2200);
});

const accountSummary = document.getElementById("account-summary");
const accountManager = document.getElementById("account-manager");

document.getElementById("manage-accounts-button").addEventListener("click", () => {
  accountSummary.classList.add("hidden");
  accountManager.classList.remove("hidden");
});

document.getElementById("close-account-manager").addEventListener("click", () => {
  accountManager.classList.add("hidden");
  accountSummary.classList.remove("hidden");
});

const connectAccountForm = document.getElementById("connect-account-form");

document.getElementById("connect-account-button").addEventListener("click", () => {
  connectAccountForm.classList.remove("hidden");
  document.getElementById("bank-select").focus();
});

document.getElementById("cancel-connect-button").addEventListener("click", () => {
  connectAccountForm.classList.add("hidden");
});

connectAccountForm.addEventListener("submit", (event) => {
  event.preventDefault();
  toast.textContent = `${document.getElementById("bank-select").value} connection started`;
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 2200);
  connectAccountForm.classList.add("hidden");
});

document.querySelectorAll(".settings-toggle").forEach((toggle) => {
  toggle.addEventListener("change", () => {
    toast.textContent = `${toggle.parentElement.textContent.trim()} ${toggle.checked ? "enabled" : "disabled"}`;
    toast.classList.add("show");
    setTimeout(() => toast.classList.remove("show"), 2200);
  });
});
