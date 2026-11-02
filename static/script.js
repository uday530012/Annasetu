function showMessage() {

    alert(
        "Welcome to FoodLoop AI! Food rescue system coming soon."
    );

}
let foodMap;
let userMarker;

function initializeMap() {

    // Hyderabad as initial center
    foodMap = L.map("foodMap").setView(
        [17.3850, 78.4867],
        12
    );

    L.tileLayer(
        "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        {
            maxZoom: 19,
            attribution:
                '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        }
    ).addTo(foodMap);

    loadFoodLocations();
}


async function loadFoodLocations() {

    try {

        const response = await fetch("/available-foods");

        const data = await response.json();

        console.log("Food locations:", data);

        if (!Array.isArray(data)) {
            console.log("Unexpected food data format");
            return;
        }

        data.forEach(food => {

            /*
             * TEMPORARY:
             * We will use coordinates when we add
             * latitude/longitude to the database.
             */

            if (
                food.latitude !== undefined &&
                food.longitude !== undefined &&
                food.latitude !== null &&
                food.longitude !== null
            ) {

                const marker = L.marker([
                    food.latitude,
                    food.longitude
                ]).addTo(foodMap);

                marker.bindPopup(`
                    <div style="min-width:200px">

                        <h3>${food.food_name}</h3>

                        <p>
                            <b>Quantity:</b>
                            ${food.quantity}
                        </p>

                        <p>
                            <b>Type:</b>
                            ${food.food_type}
                        </p>

                        <p>
                            <b>Provider:</b>
                            ${food.provider_name}
                        </p>

                        <p>
                            📍 ${food.location}
                        </p>

                    </div>
                `);
            }

        });

    } catch (error) {

        console.error(
            "Error loading food locations:",
            error
        );
    }
}


function getMyLocation() {

    if (!navigator.geolocation) {

        alert(
            "Location services are not supported by this browser."
        );

        return;
    }

    navigator.geolocation.getCurrentPosition(

        function(position) {

            const latitude =
                position.coords.latitude;

            const longitude =
                position.coords.longitude;

            console.log(
                "My location:",
                latitude,
                longitude
            );

            foodMap.setView(
                [latitude, longitude],
                15
            );

            if (userMarker) {
                foodMap.removeLayer(userMarker);
            }

            userMarker = L.marker([
                latitude,
                longitude
            ])
            .addTo(foodMap);

            userMarker.bindPopup(
                "<b>📍 Your Location</b>"
            ).openPopup();

        },

        function(error) {

            console.error(error);

            alert(
                "Unable to get your location. Please allow location permission."
            );
        }
    );
}


document.addEventListener(
    "DOMContentLoaded",
    function() {

        initializeMap();

    }
);