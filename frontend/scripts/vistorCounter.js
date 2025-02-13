// Constants
const CONFIG = {
    API_URL: 'http://localhost:7071/api/counter',
    ELEMENT_ID: 'visitor-count'
  };
  
  /**
   * Fetches and updates the visitor count with retry logic and proper error handling
   * @returns {Promise<void>}
   */
  async function updateVisitorCount() {
    try{
        const response = await fetch(CONFIG.API_URL, {
          method: 'GET',
          headers: {
            'Accept': 'application/json',
            'Cache-Control': 'no-cache'
          },
          signal: controller.signal
        });


        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }

        const count = await response.text();
        
        // Validate the response is a number
        if (!isValidVisitorCount(count)) {
          throw new Error('Invalid visitor count received');
        }

        updateDOM(count);
      }catch (error) {
        console.error("Error fetching visitor count:", error);
      }
    
  } 
  /**
   * Increments the visitor count by making a POST request
   */
  async function incrementVisitorCount() {
    try {
        const response = await fetch(CONFIG.API_URL, {
            method: 'POST',
            headers: {
                'Accept': 'application/json',
                'Content-Type': 'application/json',
                'Cache-Control': 'no-cache'
            }
        });

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const newCount = await response.text();
        updateDOM(newCount);  // Update count after incrementing
    } catch (error) {
        console.error("Error incrementing visitor count:", error);
    }
  }

  
  /**
   * Validates that the visitor count is a positive number
   * @param {string} count - The count to validate
   * @returns {boolean}
   */
  function isValidVisitorCount(count) {
    const parsed = parseInt(count, 10);
    return !isNaN(parsed) && parsed >= 0;
  }
  
  /**
   * Updates the DOM with the new count
   * @param {string} count - The count to display
   */
  function updateDOM(count) {
    const element = document.getElementById(CONFIG.ELEMENT_ID);
    if (element) {
      element.innerText = count;
    } else {
      console.error(`Element with id ${CONFIG.ELEMENT_ID} not found`);
    }
  }
  
  /**
   * Handles errors by displaying a user-friendly message
   * @param {string} message - The error message
   */
  function handleError(message) {
    const element = document.getElementById(CONFIG.ELEMENT_ID);
    if (element) {
      element.innerText = 'Unable to load visitor count';
      element.classList.add('error');
    }
    console.error(message);
  }
  
  /**
   * Creates a delay using a promise
   * @param {number} ms - Milliseconds to delay
   * @returns {Promise<void>}
   */
  function delay(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }
  
  // Initial load
  document.addEventListener('DOMContentLoaded', async () => {
    await incrementVisitorCount();  // 🔥 Increase count on reload
    await updateVisitorCount();  // 🔥 Fetch and show updated count
});
  