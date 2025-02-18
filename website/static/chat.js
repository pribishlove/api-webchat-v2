const ws = new WebSocket("ws://" + window.location.host + "/ws/chat?token=" + localStorage.getItem("jwt"));

ws.onmessage = function(event) {
    let chatBox = document.getElementById("chat-box");
    let message = document.createElement("p");
    message.textContent = event.data;
    chatBox.appendChild(message);
};

function sendMessage() {
    let input = document.getElementById("message");
    ws.send(input.value);
    input.value = "";
}
