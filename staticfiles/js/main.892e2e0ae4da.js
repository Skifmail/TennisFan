/**
 * TennisFan - Main JavaScript
 */

document.addEventListener('DOMContentLoaded', function() {
    // Mobile navigation toggle
    const navToggle = document.querySelector('.nav-toggle');
    const navMenu = document.querySelector('.nav-menu');
    
    if (navToggle && navMenu) {
        navToggle.addEventListener('click', function() {
            navMenu.classList.toggle('active');
            navToggle.classList.toggle('active');
        });
        
        // Close menu when clicking outside
        document.addEventListener('click', function(e) {
            if (!navToggle.contains(e.target) && !navMenu.contains(e.target)) {
                navMenu.classList.remove('active');
                navToggle.classList.remove('active');
                document.querySelectorAll('.nav-dropdown').forEach(dd => dd.classList.remove('open'));
            }
        });
    }

    // Mobile dropdowns by click
    document.querySelectorAll('.nav-dropdown-toggle').forEach(toggle => {
        toggle.addEventListener('click', function(e) {
            // prevent link behavior if any
            e.preventDefault();
            const parent = this.closest('.nav-dropdown');
            const isOpen = parent.classList.contains('open');
            document.querySelectorAll('.nav-dropdown').forEach(dd => dd.classList.remove('open'));
            if (!isOpen) {
                parent.classList.add('open');
            }
        });
    });
    
    // Auto-hide alerts after 5 seconds
    const alerts = document.querySelectorAll('.alert');
    alerts.forEach(function(alert) {
        setTimeout(function() {
            alert.style.opacity = '0';
            alert.style.transition = 'opacity 0.3s ease';
            setTimeout(function() {
                alert.remove();
            }, 300);
        }, 5000);
    });

    // Hide header on scroll for mobile
    let lastScrollTop = 0;
    const header = document.querySelector('.header');
    
    if (header) {
        window.addEventListener('scroll', function() {
            let scrollTop = window.pageYOffset || document.documentElement.scrollTop;
            
            // Avoid negative scroll values (iOS bounce)
            if (scrollTop < 0) {
                scrollTop = 0;
            }

            if (scrollTop > lastScrollTop && scrollTop > 60) {
                // Scrolling down & passed threshold
                header.classList.add('header-hidden');
                // Close dropdowns if open when scrolling down
                document.querySelectorAll('.nav-dropdown').forEach(dd => dd.classList.remove('open'));
                const navMenu = document.querySelector('.nav-menu');
                 if (navMenu) navMenu.classList.remove('active');
            } else {
                // Scrolling up
                header.classList.remove('header-hidden');
            }
            lastScrollTop = scrollTop;
        });
    }
});
    
    // Form filter auto-submit
    const filterForms = document.querySelectorAll('.filter-bar');
    filterForms.forEach(function(form) {
        const selects = form.querySelectorAll('select');
        selects.forEach(function(select) {
            select.addEventListener('change', function() {
                form.closest('form').submit();
            });
        });
    });
    
    // Smooth scroll for anchor links
    document.querySelectorAll('a[href^="#"]').forEach(anchor => {
        anchor.addEventListener('click', function(e) {
            const targetId = this.getAttribute('href');
            if (targetId !== '#') {
                e.preventDefault();
                const target = document.querySelector(targetId);
                if (target) {
                    target.scrollIntoView({
                        behavior: 'smooth',
                        block: 'start'
                    });
                }
            }
        });
    });
});
