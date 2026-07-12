(function () {
    // If this page was restored from the browser bfcache (Back/Forward),
    // force a reload so the server re-checks the quiz session. After submit
    // the session is gone, so the student gets redirected to the dashboard.
    window.addEventListener("pageshow", function (e) {
        if (e.persisted) {
            window.location.reload();
        }
    });

    const form = document.getElementById("quiz-form");
    if (!form) return;

    // Prevent accidental double submit on the nav buttons.
    let submitting = false;
    form.addEventListener("submit", function () {
        if (submitting) return;
        submitting = true;
        // Re-enable shortly after in case navigation didn't happen (validation etc.)
        setTimeout(function () { submitting = false; }, 1500);
    });

    // Test-level countdown timer (only present when the quiz is timed).
    const timerEl = document.getElementById("timer-display");
    const valueEl = document.getElementById("timer-value");
    if (!timerEl || !valueEl) return;

    let timeLeft = parseInt(timerEl.dataset.seconds || "0", 10);
    if (Number.isNaN(timeLeft) || timeLeft < 0) timeLeft = 0;
    valueEl.textContent = timeLeft;

    const interval = setInterval(function () {
        timeLeft -= 1;
        valueEl.textContent = Math.max(0, timeLeft);

        if (timeLeft <= 30) {
            timerEl.classList.add("danger");
        }

        if (timeLeft <= 0) {
            clearInterval(interval);
            // Auto final-submit: append a hidden action=timeout and submit.
            const hidden = document.createElement("input");
            hidden.type = "hidden";
            hidden.name = "action";
            hidden.value = "timeout";
            form.appendChild(hidden);
            form.submit();
        }
    }, 1000);
})();
