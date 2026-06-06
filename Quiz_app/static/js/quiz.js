(function () {
    const timerEl = document.getElementById("timer-display");
    const form = document.getElementById("quiz-form");
    const valueEl = document.getElementById("timer-value");
    if (!timerEl || !form || !valueEl) return;

    let timeLeft = parseInt(timerEl.dataset.seconds || "60", 10);
    if (Number.isNaN(timeLeft) || timeLeft < 0) timeLeft = 60;

    valueEl.textContent = timeLeft;

    const interval = setInterval(function () {
        timeLeft -= 1;
        valueEl.textContent = Math.max(0, timeLeft);

        if (timeLeft <= 10) {
            timerEl.classList.add("danger");
        }

        if (timeLeft <= 0) {
            clearInterval(interval);
            if (!form.dataset.submitted) {
                form.dataset.submitted = "1";
                form.submit();
            }
        }
    }, 1000);

    form.addEventListener("submit", function () {
        const btn = document.getElementById("submit-btn");
        if (btn) btn.disabled = true;
    });
})();
