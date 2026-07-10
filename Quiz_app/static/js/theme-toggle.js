// Theme Toggle Functionality
document.addEventListener('DOMContentLoaded', function() {
    const themeToggles = document.querySelectorAll('.theme-toggle-btn');
    const htmlElement = document.documentElement;
    
    // Check for saved theme preference or default to 'light'
    const currentTheme = localStorage.getItem('theme') || 'light';
    
    // Apply saved theme on page load
    if (currentTheme === 'dark') {
        htmlElement.classList.add('dark-mode');
        updateThemeIcon();
    }
    
    // Listen for toggle button clicks
    themeToggles.forEach(function(themeToggle) {
        themeToggle.addEventListener('click', function() {
            htmlElement.classList.toggle('dark-mode');
            
            // Save theme preference
            const newTheme = htmlElement.classList.contains('dark-mode') ? 'dark' : 'light';
            localStorage.setItem('theme', newTheme);
            
            updateThemeIcon();
        });
    });
    
    // Update icon based on theme
    function updateThemeIcon() {
        themeToggles.forEach(function(themeToggle) {
            const icon = themeToggle.querySelector('i');
            if (!icon) return;
            if (htmlElement.classList.contains('dark-mode')) {
                icon.className = 'bi bi-sun-fill';
                themeToggle.title = 'Switch to Light Mode';
            } else {
                icon.className = 'bi bi-moon-stars-fill';
                themeToggle.title = 'Switch to Dark Mode';
            }
        });
    }

    updateThemeIcon();
});
