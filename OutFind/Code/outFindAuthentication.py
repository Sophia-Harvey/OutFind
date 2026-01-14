struct AuthenticationView: View {
    @State private var isLogin = true
    
    var body: some View {
        VStack {
            Image("app_logo")
                .resizable()
                .scaledToFit()
                .frame(width: 200)
            
            if isLogin {
                LoginView()
            } else {
                SignUpView()
            }
            
            Button(action: {
                isLogin.toggle()
            }) {
                Text(isLogin ? "New user? Create account" : "Already have an account? Login")
                    .foregroundColor(.blue)
            }
            .padding()
        }
    }
} 