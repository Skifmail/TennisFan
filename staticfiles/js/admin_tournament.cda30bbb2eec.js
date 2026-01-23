/**
 * Auto-calculate points_loser based on points_winner
 */

(function() {
    'use strict';

    // Wait for DOM to be ready
    document.addEventListener('DOMContentLoaded', function() {
        const winnerInput = document.getElementById('id_points_winner');
        const loserInput = document.getElementById('id_points_loser');

        if (winnerInput && loserInput) {
            // Function to calculate loser points
            function calculateLoserPoints() {
                const winnerPoints = parseInt(winnerInput.value) || 0;
                const calculatedLoserPoints = -Math.floor(winnerPoints / 2);
                
                // Only update if loser field is empty or has the default calculation
                // This allows manual override
                if (loserInput.value === '' || 
                    loserInput.dataset.autoCalculated === 'true') {
                    loserInput.value = calculatedLoserPoints;
                    loserInput.dataset.autoCalculated = 'true';
                }
            }

            // Track manual changes
            loserInput.addEventListener('input', function() {
                loserInput.dataset.autoCalculated = 'false';
            });

            // Calculate on winner input change
            winnerInput.addEventListener('input', calculateLoserPoints);

            // Initial calculation
            if (loserInput.value === '' || loserInput.value === '-50') {
                loserInput.dataset.autoCalculated = 'true';
                calculateLoserPoints();
            }
        }
    });
})();
