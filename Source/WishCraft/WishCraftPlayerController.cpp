// Copyright Epic Games, Inc. All Rights Reserved.


#include "WishCraftPlayerController.h"
#include "Abilities/AbilityLoadoutManager.h"
#include "Abilities/AbilityTokenTable.h"
#include "Engine/World.h"
#include "Kismet/GameplayStatics.h"
#include "Sound/SoundBase.h"
#include "Blueprint/UserWidget.h"
#include "EnhancedInputSubsystems.h"
#include "Engine/LocalPlayer.h"
#include "InputMappingContext.h"
#include "Rules/RuleParserLibrary.h"
#include "WishCraft.h"
#include "Widgets/Input/SVirtualJoystick.h"
#include "Dom/JsonObject.h"
#include "HttpModule.h"
#include "Interfaces/IHttpRequest.h"
#include "Interfaces/IHttpResponse.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

AWishCraftPlayerController::AWishCraftPlayerController()
{
	// A default subobject so the manager exists on the server AND on every client.
	AbilityLoadoutManager = CreateDefaultSubobject<UAbilityLoadoutManager>(TEXT("AbilityLoadoutManager"));
}

void AWishCraftPlayerController::BeginPlay()
{
	Super::BeginPlay();

	// only spawn touch controls on local player controllers
	if (IsLocalPlayerController() && ShouldUseTouchControls())
	{
		// spawn the mobile controls widget
		MobileControlsWidget = CreateWidget<UUserWidget>(this, MobileControlsWidgetClass);

		if (MobileControlsWidget)
		{
			// add the controls to the player screen
			MobileControlsWidget->AddToPlayerScreen(0);

		} else {

			UE_LOG(LogWishCraft, Error, TEXT("Could not spawn mobile controls widget."));

		}

	}

	// The host gets the rule panel up front so it can type the rules before playing.
	if (HasAuthority() && IsLocalPlayerController())
	{
		UE_LOG(LogSandbox, Log, TEXT("Host controller ready - opening the rule panel"));
		SetRuleWidgetVisible(true);
	}
}

void AWishCraftPlayerController::OnPossess(APawn* InPawn)
{
	Super::OnPossess(InPawn);

	if (!AbilityLoadoutManager)
	{
		return;
	}

	// The rules live on the GameState, so any controller - including one that joined after the host
	// typed them - pulls them from that single replicated source. A fresh spawn starts with empty
	// States, so every cooldown is expired by design.
	AbilityLoadoutManager->PullRulesetFromGameState();
	AbilityLoadoutManager->RefreshAbilityComponents();
}

void AWishCraftPlayerController::AcknowledgePossession(APawn* InPawn)
{
	Super::AcknowledgePossession(InPawn);

	if (AbilityLoadoutManager)
	{
		AbilityLoadoutManager->RefreshAbilityComponents();
	}
}

void AWishCraftPlayerController::SetupInputComponent()
{
	Super::SetupInputComponent();

	// only add IMCs for local player controllers
	if (IsLocalPlayerController())
	{
		// Add Input Mapping Contexts
		if (UEnhancedInputLocalPlayerSubsystem* Subsystem = ULocalPlayer::GetSubsystem<UEnhancedInputLocalPlayerSubsystem>(GetLocalPlayer()))
		{
			for (UInputMappingContext* CurrentContext : DefaultMappingContexts)
			{
				Subsystem->AddMappingContext(CurrentContext, 0);
			}

			// only add these IMCs if we're not using mobile touch input
			if (!ShouldUseTouchControls())
			{
				for (UInputMappingContext* CurrentContext : MobileExcludedMappingContexts)
				{
					Subsystem->AddMappingContext(CurrentContext, 0);
				}
			}
		}
	}
}

void AWishCraftPlayerController::EndPlay(const EEndPlayReason::Type EndPlayReason)
{
	if (PendingIntentRequest.IsValid())
	{
		PendingIntentRequest->OnProcessRequestComplete().Unbind();
		PendingIntentRequest->CancelRequest();
		PendingIntentRequest.Reset();
	}
	bRuleRequestPending = false;
	Super::EndPlay(EndPlayReason);
}

bool AWishCraftPlayerController::SubmitRuleText(const FString& RuleText, FString& OutError)
{
	OutError.Empty();
	if (!HasAuthority() || !IsLocalPlayerController())
	{
		OutError = TEXT("Only the host can edit rules.");
		return false;
	}
	if (bRuleRequestPending)
	{
		OutError = TEXT("A rule request is already being checked. Please wait.");
		return false;
	}
	const UAbilityTokenTable* Table = AbilityLoadoutManager ? AbilityLoadoutManager->GetTokenTable() : nullptr;
	if (!Table || RuleText.TrimStartAndEnd().IsEmpty() || RuleText.Len() > 2048)
	{
		OutError = TEXT("Type a rule of 1 to 2048 characters and make sure the ability list is loaded.");
		return false;
	}

	TSharedRef<FJsonObject> Payload = MakeShared<FJsonObject>();
	Payload->SetStringField(TEXT("text"), RuleText);
	TArray<TSharedPtr<FJsonValue>> Catalogue;
	for (const FName Name : Table->GetAllTokenNames())
	{
		const FAbilityTokenDefinition* Definition = Table->FindToken(Name);
		TSharedRef<FJsonObject> Token = MakeShared<FJsonObject>();
		Token->SetStringField(TEXT("name"), Name.ToString());
		Token->SetStringField(TEXT("display_name"), Definition->GetDisplayNameString());
		Token->SetStringField(TEXT("category"), Definition->Category == EAbilityCategory::Defense
			? TEXT("Defense") : Definition->Category == EAbilityCategory::Mobility ? TEXT("Mobility") : TEXT("Offense"));
		TArray<TSharedPtr<FJsonValue>> Keywords;
		for (const FString& Keyword : Definition->Keywords)
		{
			Keywords.Add(MakeShared<FJsonValueString>(Keyword));
		}
		Token->SetArrayField(TEXT("keywords"), Keywords);
		// Names only: numeric ranges remain on the authoritative game side.
		TSharedRef<FJsonObject> Ranges = MakeShared<FJsonObject>();
		for (const TPair<FName, FAbilityParamRange>& Param : Definition->ParamRanges)
		{
			Ranges->SetObjectField(Param.Key.ToString(), MakeShared<FJsonObject>());
		}
		Token->SetObjectField(TEXT("ranges"), Ranges);
		Catalogue.Add(MakeShared<FJsonValueObject>(Token));
	}
	Payload->SetArrayField(TEXT("tokens"), Catalogue);
	FString Body;
	const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Body);
	FJsonSerializer::Serialize(Payload, Writer);

	const FHttpRequestRef Request = FHttpModule::Get().CreateRequest();
	PendingIntentRequest = Request;
	bRuleRequestPending = true;
	Request->SetURL(IntentServiceURL);
	Request->SetVerb(TEXT("POST"));
	Request->SetHeader(TEXT("Content-Type"), TEXT("application/json"));
	Request->SetContentAsString(Body);
	Request->SetTimeout(10.f);
	Request->SetDelegateThreadPolicy(EHttpRequestDelegateThreadPolicy::CompleteOnGameThread);
	Request->OnProcessRequestComplete().BindWeakLambda(this,
		[this, RuleText](FHttpRequestPtr Completed, FHttpResponsePtr Response, bool bConnected)
		{
			if (PendingIntentRequest != Completed || !bRuleRequestPending) { return; }
			PendingIntentRequest.Reset();
			if (!bConnected || !Response.IsValid() || Response->GetResponseCode() != 200)
			{
				ApplyKeywordFallback(RuleText, TEXT("model service unavailable"));
				return;
				}
				const FString Json = Response->GetContentAsString();
				// Which layer answered (exclusion / clauses / model / no-catalogue). This arrives as a
				// header, not a body field, because the body shape is load-bearing: the empty-list
				// sentinel below and ULoadoutValidator::ValidateIntentJson both require the root
				// object to hold exactly one field, so an extra JSON key would be rejected outright.
				// FHttpResponseCommon::GetHeader does a case-SENSITIVE TMap lookup, and uvicorn/
				// Starlette emit header names lowercased, so asking only for the canonical
				// spelling silently returned empty and every response logged the
				// header-absent branch. Try both spellings.
				FString Source = Response->GetHeader(TEXT("X-Intent-Source"));
				if (Source.IsEmpty())
				{
					Source = Response->GetHeader(TEXT("x-intent-source"));
				}
				TSharedPtr<FJsonObject> Root;
				const TArray<TSharedPtr<FJsonValue>>* Entries = nullptr;
				const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Json);
			if (Json.Len() <= 65536 && FJsonSerializer::Deserialize(Reader, Root) && Root.IsValid()
				&& Root->Values.Num() == 1 && Root->TryGetArrayField(TEXT("tokens"), Entries) && Entries->IsEmpty())
			{
				ApplyKeywordFallback(RuleText, TEXT("model abstained"));
				return;
				}
				FString Error;
				const bool bApplied = AbilityLoadoutManager && AbilityLoadoutManager->ServerSetIntentJson(Json, Error);
				FinishRuleRequest(bApplied, bApplied
					? (Source.IsEmpty()
						? FString(TEXT("Rules applied (intent model)."))
						: FString::Printf(TEXT("Rules applied (intent model: %s)."), *Source))
					: FString::Printf(TEXT("Rules rejected: %s"), *Error));
			});
	UE_LOG(LogSandbox, Log, TEXT("Intent request started: %s"), *RuleText);
	if (!Request->ProcessRequest() && bRuleRequestPending)
	{
		Request->OnProcessRequestComplete().Unbind();
		PendingIntentRequest.Reset();
		ApplyKeywordFallback(RuleText, TEXT("model request could not start"));
	}
	return true;
}

void AWishCraftPlayerController::ApplyKeywordFallback(const FString& RuleText, const FString& Reason)
{
	UE_LOG(LogSandbox, Warning, TEXT("Intent keyword fallback: %s"), *Reason);
	FString Error;
	TArray<FName> Tokens;
	const UAbilityTokenTable* Table = AbilityLoadoutManager ? AbilityLoadoutManager->GetTokenTable() : nullptr;
	const bool bApplied = URuleParserLibrary::ParseRuleText(RuleText, Table, Tokens, Error)
		&& AbilityLoadoutManager->ServerSetLoadout(Tokens, Error);
	FinishRuleRequest(bApplied, bApplied
		? FString::Printf(TEXT("Rules applied (keyword fallback: %s)."), *Reason)
		: FString::Printf(TEXT("Rules rejected: %s"), *Error));
}

void AWishCraftPlayerController::FinishRuleRequest(bool bApplied, const FString& Message)
{
	bRuleRequestPending = false;
	PendingIntentRequest.Reset();

	// Local confirmation tone. SubmitRuleText is authority-only, so the machine that reaches here is
	// the host's own, and a rule panel is that host's screen rather than a world event - hence 2D.
	if (bApplied)
	{
		static TWeakObjectPtr<USoundBase> CachedConfirmSound;
		if (!CachedConfirmSound.IsValid())
		{
			CachedConfirmSound = LoadObject<USoundBase>(nullptr, TEXT("/Game/Sandbox/Audio/SFX_UI_Confirm.SFX_UI_Confirm"));
		}

		if (USoundBase* ConfirmSound = CachedConfirmSound.Get())
		{
			UGameplayStatics::PlaySound2D(this, ConfirmSound);
		}
	}
	UE_LOG(LogSandbox, Log, TEXT("Intent request finished (%s): %s"), bApplied ? TEXT("applied") : TEXT("unchanged"), *Message);
	OnRuleResolution.Broadcast(bApplied, Message);
	ClientMessage(Message, NAME_None, 6.f);
}

void AWishCraftPlayerController::RulesModel(const FString& RuleText)
{
	FString Error;
	if (!SubmitRuleText(RuleText, Error))
	{
		ClientMessage(Error, NAME_None, 6.f);
		UE_LOG(LogSandbox, Warning, TEXT("Intent request rejected: %s"), *Error);
	}
}

bool AWishCraftPlayerController::ApplyRuleText(const FString& RuleText, FString& OutError)
{
	OutError.Empty();
	if (bRuleRequestPending)
	{
		OutError = TEXT("Wait for the pending model request before changing rules.");
		return false;
	}

	if (!AbilityLoadoutManager)
	{
		OutError = TEXT("The loadout manager is missing on this controller.");
		return false;
	}

	const UAbilityTokenTable* Table = AbilityLoadoutManager->GetTokenTable();

	TArray<FName> Tokens;
	if (!URuleParserLibrary::ParseRuleText(RuleText, Table, Tokens, OutError))
	{
		return false;
	}

	return AbilityLoadoutManager->ServerSetLoadout(Tokens, OutError);
}

bool AWishCraftPlayerController::ApplyRawTokens(const TArray<FName>& Tokens, FString& OutError)
{
	OutError.Empty();
	if (bRuleRequestPending)
	{
		OutError = TEXT("Wait for the pending model request before changing rules.");
		return false;
	}

	if (!AbilityLoadoutManager)
	{
		OutError = TEXT("The loadout manager is missing on this controller.");
		return false;
	}

	return AbilityLoadoutManager->ServerSetLoadout(Tokens, OutError);
}

void AWishCraftPlayerController::Rules(const FString& RuleText)
{
	FString Error;
	const bool bApplied = ApplyRuleText(RuleText, Error);

	UE_LOG(LogSandbox, Log, TEXT("Rules \"%s\" => %s"), *RuleText, bApplied ? TEXT("applied") : *Error);

	ClientMessage(bApplied ? FString(TEXT("Rules applied.")) : FString::Printf(TEXT("Rules rejected: %s"), *Error), NAME_None, 6.f);
}

void AWishCraftPlayerController::RulesRaw(const FString& CommaSeparatedTokens)
{
	const TArray<FName> Tokens = URuleParserLibrary::ParseTokenList(CommaSeparatedTokens);

	FString Error;
	const bool bApplied = ApplyRawTokens(Tokens, Error);

	UE_LOG(LogSandbox, Log, TEXT("RulesRaw \"%s\" => %s"), *CommaSeparatedTokens, bApplied ? TEXT("applied") : *Error);

	ClientMessage(bApplied ? FString(TEXT("Rules applied.")) : FString::Printf(TEXT("Rules rejected: %s"), *Error), NAME_None, 6.f);
}

void AWishCraftPlayerController::RuleUI()
{
	ToggleRuleWidget();
}

void AWishCraftPlayerController::ToggleRuleWidget()
{
	const bool bCurrentlyVisible = RuleWidget && RuleWidget->IsInViewport();
	SetRuleWidgetVisible(!bCurrentlyVisible);
}

void AWishCraftPlayerController::SetRuleWidgetVisible(bool bVisible)
{
	if (!IsLocalPlayerController())
	{
		return;
	}

	if (bVisible)
	{
		if (!RuleWidget)
		{
			UClass* WidgetClass = RuleWidgetClass ? RuleWidgetClass.Get() : nullptr;
			if (!WidgetClass)
			{
				WidgetClass = StaticLoadClass(UUserWidget::StaticClass(), nullptr, TEXT("/Game/Sandbox/UI/WBP_RuleInput.WBP_RuleInput_C"));
			}

			if (!WidgetClass)
			{
				UE_LOG(LogSandbox, Warning, TEXT("Rule widget class not found (expected /Game/Sandbox/UI/WBP_RuleInput)"));
				return;
			}

			RuleWidget = CreateWidget<UUserWidget>(this, WidgetClass);
		}

		if (!RuleWidget)
		{
			return;
		}

		if (!RuleWidget->IsInViewport())
		{
			RuleWidget->AddToPlayerScreen(10);
		}

		FInputModeGameAndUI InputMode;
		InputMode.SetHideCursorDuringCapture(false);
		SetInputMode(InputMode);
		bShowMouseCursor = true;

		UE_LOG(LogSandbox, Log, TEXT("Rule panel shown"));
	}
	else
	{
		if (RuleWidget && RuleWidget->IsInViewport())
		{
			RuleWidget->RemoveFromParent();
		}

		SetInputMode(FInputModeGameOnly());
		bShowMouseCursor = false;

		UE_LOG(LogSandbox, Log, TEXT("Rule panel hidden - gameplay input restored"));
	}
}

bool AWishCraftPlayerController::ShouldUseTouchControls() const
{
	// are we on a mobile platform? Should we force touch?
	return SVirtualJoystick::ShouldDisplayTouchInterface() || bForceTouchControls;
}
